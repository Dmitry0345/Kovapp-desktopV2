# -*- coding: utf-8 -*-

from typing import List, Optional, Dict, Literal
from datetime import datetime
import mimetypes
from starlette.exceptions import HTTPException as StarletteHTTPException
import asyncio
import gzip
import uuid
from urllib.parse import quote
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session, joinedload
from pathlib import Path
from fastapi import Request

from server.database.models import *

from fastapi import APIRouter

from ..config import FILE_STORAGE_ROOT, logger, video_static_app
from ..core import (get_chat_db, get_current_user_id, get_current_user_id_from_header_or_query,
                    get_db, issue_file_token, task_is_archived, user_can_access_task,
                    user_can_read_task_files, user_has_full_task_access,
                    user_is_department_head_for_task, user_leads_stage)
from ..realtime import manager
from ..schemas import FileInfo, FileOut, TaskStageFileOut
from ..storage import (_storage_path, delete_file_from_disk, disposition_for,
                       office_preview_response,
                       get_decompressed_file_response, safe_content_type,
                       get_decompressed_file_response_with_range, get_task_folder_name,
                       is_video_filename, process_video_sync,
                       purge_quarantined_files, quarantine_files_for_deletion,
                       resolve_stored_path, restore_quarantined_files,
                       save_compressed_file_to_disk)
from .. import task_journal as journal
from ..sheets import sheet_json
from .. import media
from .. import file_access
from ..media import ensure_thumbnail


router = APIRouter()

def _kind_of(name: str) -> str:
    if media.is_video_name(name):
        return "video"
    if media.is_image_name(name):
        return "image"
    return "document"


ANNOUNCE_WINDOW = 1.5
_announce: dict = {}


async def announce_task_files(task_id: int, db: Session) -> None:
    """Лёгкий сигнал всем: у задачи изменился состав файлов.

    Событие о файле рассылалось только участникам задачи, а раздел
    «Медиаконтент» проекта смотрят и те, кто в задаче не состоит: у них
    папки не обновлялись до перезагрузки. Здесь ни файла, ни прав — клиент
    перечитает список сам и покажет ровно то, что ему положено видеть.
    При массовой загрузке сигналы склеиваются: первый уходит сразу, затем не
    чаще раза в ANNOUNCE_WINDOW секунд на задачу — иначе 100 файлов дали бы
    каждому клиенту 100 перечитываний списка.
    """
    import asyncio
    import time
    try:
        project_id = db.query(Task.project_id).filter(Task.id == task_id).scalar()
    except Exception:
        project_id = None
    state = _announce.get(task_id)
    if state and time.monotonic() < state["until"]:
        state["dirty"] = True
        return
    _announce[task_id] = {"until": time.monotonic() + ANNOUNCE_WINDOW, "dirty": False}
    await _send_files_changed(task_id, project_id)

    async def trailing():
        while True:
            await asyncio.sleep(ANNOUNCE_WINDOW)
            state = _announce.get(task_id)
            if not state or not state["dirty"]:
                _announce.pop(task_id, None)
                return
            state["dirty"] = False
            state["until"] = time.monotonic() + ANNOUNCE_WINDOW
            await _send_files_changed(task_id, project_id)
    asyncio.get_running_loop().create_task(trailing())


async def _send_files_changed(task_id: int, project_id) -> None:
    try:
        await manager.broadcast({"type": "task.files_changed", "task_id": task_id, "project_id": project_id})
    except Exception as e:
        logger.debug("Не удалось разослать сигнал об изменении файлов задачи: %s", e)


def parse_edits(raw: Optional[str]) -> dict:
    """Правки из окна отправки: {index: {trim_start, trim_end, crop:{x,y,w,h}}}.

    Фото правятся на устройстве и приходят уже готовыми. Видео браузер
    перекодировать не может, поэтому клиент присылает только точки обрезки
    и рамку кадра — сервер применяет их в том же проходе ffmpeg, который
    и так пережимает видео. Лишней нагрузки это не создаёт.
    """
    if not raw:
        return {}
    try:
        import json
        data = json.loads(raw)
        return {str(k): v for k, v in (data or {}).items() if isinstance(v, dict)}
    except Exception:
        return {}


async def _store_upload(folder_name: str, upload: UploadFile, upload_id: Optional[str] = None,
                        index: int = 0, edit: Optional[dict] = None) -> media.StoredFile:
    """Сохраняет загруженный файл через общий конвейер media.py.

    Всё тяжёлое — перекодировка видео, сжатие фото, архивация — идёт через
    общую очередь upload_queue: одновременно обрабатывается ограниченное
    число файлов, остальные ждут. Ход обработки виден клиенту по upload_id.
    """
    from .. import upload_queue as queue
    folder = Path(FILE_STORAGE_ROOT) / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    original_name = Path(upload.filename or "file").name
    kind = _kind_of(original_name)

    if kind == "video":
        temp_suffix = Path(original_name).suffix or ".video"
        temp_path = folder / f".{uuid.uuid4().hex}{temp_suffix}.uploading"
        try:
            queue.update(upload_id, index, "receiving", 5, "Приём файла")
            with open(temp_path, "wb") as buffer:
                while chunk := await upload.read(1024 * 1024):
                    buffer.write(chunk)
            queue.update(upload_id, index, "queued", 10)
            # ffmpeg сам сообщает готовность — процент настоящий, не оценка
            def _tick(pct, _i=index):
                queue.update(upload_id, _i, "processing", pct, "Перекодировка видео")

            stored = await queue.run_processing(
                upload_id, index, media.store_video_from_temp, folder, original_name, temp_path,
                edit or {}, _tick, note="Перекодировка видео")
            queue.update(upload_id, index, "done", 100, "Готово")
            return stored
        except HTTPException:
            queue.update(upload_id, index, "error", None, "Ошибка")
            raise
        except Exception as e:
            queue.update(upload_id, index, "error", None, "Ошибка перекодировки")
            logger.exception(f"Не удалось перекодировать видео: {e}")
            raise HTTPException(
                status_code=422,
                detail=("Не удалось подготовить видео к воспроизведению. "
                        "Проверьте, что на сервере установлен ffmpeg с кодеками "
                        "libvpx-vp9/libopus."))
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    logger.exception(f"Не удалось удалить временный файл: {temp_path}")

    # Фото и документы читаем кусками во временный файл, а не целиком в
    # память: при десятке крупных снимков это экономит сотни мегабайт
    queue.update(upload_id, index, "receiving", 5, "Приём файла")
    temp = folder / f".{uuid.uuid4().hex}.uploading"
    try:
        with open(temp, "wb") as buffer:
            while chunk := await upload.read(1024 * 1024):
                buffer.write(chunk)
        queue.update(upload_id, index, "queued", 10)
        if kind == "image":
            def _img():
                content = temp.read_bytes()
                return media.store_image(folder, original_name, content)
            stored = await queue.run_processing(upload_id, index, _img, note="Сжатие фото")
        else:
            def _doc():
                content = temp.read_bytes()
                return media.store_document(folder, original_name, content)
            stored = await queue.run_processing(upload_id, index, _doc, note="Архивация")
        queue.update(upload_id, index, "done", 100, "Готово")
        return stored
    except Exception:
        queue.update(upload_id, index, "error", None, "Ошибка")
        raise
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except Exception as e:
                logger.debug("Загрузка: временный файл не удалён: %s", e)


async def _store_video_as_webm(task_id: int, task_name: str, upload: UploadFile):
    """Совместимая обёртка над _store_upload (старый интерфейс)."""
    stored = await _store_upload(get_task_folder_name(task_id, task_name), upload)
    return stored.relative_path, stored.display_name, stored.extension, stored.size


def _ensure_stage_read(db: Session, user_id: int, file) -> None:
    """Файл этапа — руководству и сотрудникам отдела этапа (как сам этап в окне задачи)."""
    if user_has_full_task_access(db, user_id):
        return
    stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == file.stage_detail_id).first()
    emp = db.query(Employee).filter(Employee.id == user_id).first()
    mine = (getattr(emp, "department", "") or "").strip()
    if not stage or (stage.department_name or "").strip() != mine:
        raise HTTPException(status_code=403, detail="Файлы этапа другого отдела недоступны")


def _task_for_stage_file(db: Session, file: TaskStageFile):
    stage = db.query(TaskStageDetails).filter(
        TaskStageDetails.id == file.stage_detail_id
    ).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Этап файла не найден")
    task = db.query(Task).filter(Task.id == stage.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача файла не найдена")
    return task


def _ensure_task_access(db: Session, current_user_id: int, task: Task) -> None:
    if not user_can_access_task(db, current_user_id, task):
        raise HTTPException(status_code=403, detail="Нет доступа к файлам задачи")


def _ensure_task_read(db: Session, current_user_id: int, task: Task) -> None:
    if not user_can_read_task_files(db, current_user_id, task):
        raise HTTPException(status_code=403, detail="Нет доступа к файлам задачи")


def _ensure_can_upload(db: Session, current_user_id: int, task: Task, stage) -> None:
    """Файлы задачи добавляет любой, у кого есть доступ к задаче (проверен раньше); файлы этапа — его отдел."""
    if task_is_archived(task):
        raise HTTPException(status_code=409, detail="Задача в архиве — файлы изменить нельзя")
    if stage is not None:
        if stage.is_completed:
            raise HTTPException(status_code=409, detail="Этап завершён — возобновите его, чтобы добавить файлы")
        if not user_leads_stage(db, current_user_id, stage):
            raise HTTPException(status_code=403, detail="Файлы этапа добавляет ведущий его отдел")


def _uploader_dict(emp):
    if emp is None:
        return None
    return {"id": emp.id, "last_name": emp.last_name, "first_name": emp.first_name,
            "middle_name": emp.middle_name, "department": emp.department}


def _effective_filename(file) -> str:
    # видео лежит в перекодированном виде — отдаём с настоящим расширением
    suffix = Path(str(file.filepath or "")).suffix.lower()
    if suffix in {".webm", ".mp4"}:
        return f"{Path(file.filename).stem}{suffix}"
    return file.filename


def _is_raw_video(file) -> bool:
    return Path(str(file.filepath or "")).suffix.lower() in {".webm", ".mp4", ".ogg"}


async def _chat_style_video_response(file, filename: str, request: Request,
                                     download: bool = False):
    relative_path = str(file.filepath or "").replace("\\", "/").lstrip("/")
    try:
        response = await video_static_app.get_response(relative_path, request.scope)
    except StarletteHTTPException as exc:
        if exc.status_code != 404:
            raise HTTPException(status_code=exc.status_code, detail=str(exc.detail))
        # в основном хранилище нет: общий путь найдёт файл в дополнительной папке или ответит 404
        return get_decompressed_file_response_with_range(request, file.filepath, filename, download=download)
    suffix = Path(relative_path).suffix.lower()
    response.headers["Content-Type"] = {
        ".webm": "video/webm",
        ".mp4": "video/mp4",
        ".ogg": "video/ogg",
    }.get(suffix, "application/octet-stream")
    response.headers["Content-Disposition"] = disposition_for(filename, download)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/upload", response_model=List[FileOut])
async def upload_files(
        files: List[UploadFile] = File(None),
        thumbs: List[UploadFile] = File(None),
        meta: Optional[str] = Form(None),
        task_id: Optional[int] = Form(None),
        stage_detail_id: Optional[int] = Form(None),
        upload_id: Optional[str] = Form(None),
        edits: Optional[str] = Form(None),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    from .. import upload_queue as queue
    from .. import intake
    items = intake.parse_items(files, thumbs, meta)
    if not items:
        raise HTTPException(status_code=400, detail="Нет файлов для загрузки")
    queue.start(upload_id, [intake.describe(i) for i in items])
    edit_map = parse_edits(edits)

    task = None
    stage = None
    # Клиент присылает task_id вместе со stage_detail_id, поэтому этап
    # разбираем первым: иначе stage оставался None, файл создавался как
    # файл этапа, а рассылка падала на stage.task_id и молча гасилась
    if stage_detail_id:
        stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == stage_detail_id).first()
        if not stage:
            raise HTTPException(status_code=404, detail="Этап не найден")
        if task_id and stage.task_id != task_id:
            raise HTTPException(status_code=400, detail="Этап не принадлежит указанной задаче")
        task = db.query(Task).filter(Task.id == stage.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Связанная задача не найдена")
    elif task_id:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")
    else:
        raise HTTPException(status_code=400, detail="Не указана задача или этап")
    _ensure_task_access(db, current_user_id, task)
    if task.project_archived:   # завершённый проект — только чтение
        raise HTTPException(status_code=409, detail="Проект завершён — действия недоступны. Сначала верните его в работу.")
    _ensure_can_upload(db, current_user_id, task, stage)

    created_files = []
    created_paths = []
    try:
        folder_name = get_task_folder_name(task.id, task.name)
        infos = []
        for index, item in enumerate(items):
            stored, info = await intake.store_item(folder_name, item, upload_id, index, edit_map.get(str(index)),
                                                   current_user_id, db)
            relative_path = stored.relative_path
            created_paths.append(relative_path)

            db_file = None
            if stage_detail_id:
                db_file = TaskStageFile(
                    stage_detail_id=stage_detail_id,
                    filename=stored.display_name,
                    filepath=relative_path,
                    size=stored.size,
                    extension=stored.extension,
                    uploaded_by_id=current_user_id,
                    created_at=datetime.now(),
                )
            else:
                db_file = TaskFile(
                    task_id=task.id,
                    filename=stored.display_name,
                    filepath=relative_path,
                    size=stored.size,
                    extension=stored.extension,
                    uploaded_by_id=current_user_id,
                    created_at=datetime.now(),
                )

            if db_file:
                db.add(db_file)
                created_files.append(db_file)
                infos.append(info)

        db.flush()
        for created_file in created_files:
            db.refresh(created_file)
        for created_file, info in zip(created_files, infos):
            intake.apply_meta(created_file, info)
        # Журнал задачи: прикрепление тоже отмечаем, одной строкой на пачку
        if created_files and task:
            stage_files = [f for f in created_files if isinstance(f, TaskStageFile)]
            task_files = [f for f in created_files if not isinstance(f, TaskStageFile)]
            if task_files:
                journal.note(db, task.id, "task_file",
                             "добавлены: " + ", ".join("«%s»" % f.filename for f in task_files),
                             current_user_id)
            if stage_files:
                journal.note(db, task.id, "stage_file",
                             "добавлены в этап: " + ", ".join("«%s»" % f.filename for f in stage_files),
                             current_user_id)
        db.commit()

        try:
            for f in created_files:
                if isinstance(f, TaskStageFile):
                    payload = {
                        "type": "stage.file_added",
                        "task_id": task.id,
                        "stage_id": f.stage_detail_id,
                        "file_id": f.id,
                        "filename": f.filename,
                        "sender_id": current_user_id,
                        "uploaded_by_id": current_user_id
                    }
                else:
                    payload = {
                        "type": "task.file_added",
                        "task_id": task.id,
                        "file_id": f.id,
                        "filename": f.filename,
                        "sender_id": current_user_id,
                        "uploaded_by_id": current_user_id
                    }
                await manager.broadcast_to_task_participants(task.id, payload, db)
            await announce_task_files(task.id, db)
        except Exception as e:
            # Раньше сбой рассылки гасился молча, и файл просто не появлялся у коллег
            logger.error(f"Не удалось разослать уведомление о загрузке файлов: {e}")

        queue.finish(upload_id)
        return created_files
    except HTTPException:
        db.rollback()
        for created_path in created_paths:
            delete_file_from_disk(created_path)
        raise
    except Exception as e:
        db.rollback()
        for created_path in created_paths:
            delete_file_from_disk(created_path)
        raise HTTPException(status_code=500, detail=f"Ошибка при загрузке файлов: {str(e)}")




@router.get("/tasks/{task_id}/files", response_model=List[FileOut])
def get_task_files(
        task_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        _ensure_task_read(db, current_user_id, task)

        files = db.query(TaskFile).options(joinedload(TaskFile.uploaded_by)).filter(
            TaskFile.task_id == task_id).all()
        return files
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при получении файлов: {str(e)}")


@router.get("/tasks/{task_id}/stages/{stage_id}/files", response_model=List[FileOut])
def get_stage_files(
        task_id: int,
        stage_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        stage = db.query(TaskStageDetails).filter(
            TaskStageDetails.id == stage_id,
            TaskStageDetails.task_id == task_id
        ).first()
        if not stage:
            raise HTTPException(status_code=404, detail="Этап не найден")
        task = db.query(Task).filter(Task.id == stage.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        _ensure_task_read(db, current_user_id, task)

        files = db.query(TaskStageFile).options(joinedload(TaskStageFile.uploaded_by)).filter(
            TaskStageFile.stage_detail_id == stage_id).all()
        return files
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при получении файлов: {str(e)}")


def _thumbnail_response(file, filename: str):
    """Миниатюра фото/видео; создаётся при первом обращении для старых файлов."""
    try:
        stored = resolve_stored_path(file.filepath)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    thumb = ensure_thumbnail(stored, filename)
    if thumb is None:
        raise HTTPException(status_code=404, detail="Миниатюра недоступна")
    return FileResponse(str(thumb), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400",
                                 "X-Content-Type-Options": "nosniff"})


@router.get("/task/files/{file_id}/thumb")
def task_file_thumbnail(
        file_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    file = db.query(TaskFile).filter(TaskFile.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="Файл задачи не найден")
    task = db.query(Task).filter(Task.id == file.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача файла не найдена")
    _ensure_task_read(db, current_user_id, task)
    return _thumbnail_response(file, _effective_filename(file))


@router.get("/stages/files/{file_id}/thumb")
def stage_file_thumbnail(
        file_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    file = db.query(TaskStageFile).filter(TaskStageFile.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="Файл этапа не найден")
    task = _task_for_stage_file(db, file)
    _ensure_task_read(db, current_user_id, task)
    _ensure_stage_read(db, current_user_id, file)
    return _thumbnail_response(file, _effective_filename(file))


@router.get("/task/files/{file_id}/download")
async def download_task_file(
        file_id: int,
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)
):
    try:
        file = db.query(TaskFile).filter(TaskFile.id == file_id).first()
        if not file:
            raise HTTPException(status_code=404, detail="Файл задачи не найден")
        task = db.query(Task).filter(Task.id == file.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача файла не найдена")
        _ensure_task_read(db, current_user_id, task)

        filename = _effective_filename(file)
        if _is_raw_video(file):
            return await _chat_style_video_response(
                file, filename, request, download=True)
        if is_video_filename(filename):
            return get_decompressed_file_response_with_range(
                request, file.filepath, filename, download=True)
        return get_decompressed_file_response(file.filepath, filename, download=True, request=request)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при скачивании файла задачи: {str(e)}"
        )


@router.get("/stages/files/{file_id}/download")
async def download_stage_file(
        file_id: int,
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)
):
    try:
        file = db.query(TaskStageFile).filter(TaskStageFile.id == file_id).first()
        if not file:
            raise HTTPException(status_code=404, detail="Файл этапа не найден")
        task = _task_for_stage_file(db, file)
        _ensure_task_read(db, current_user_id, task)
        _ensure_stage_read(db, current_user_id, file)

        filename = _effective_filename(file)
        if _is_raw_video(file):
            return await _chat_style_video_response(
                file, filename, request, download=True)
        if is_video_filename(filename):
            return get_decompressed_file_response_with_range(
                request, file.filepath, filename, download=True)
        return get_decompressed_file_response(file.filepath, filename, download=True, request=request)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при скачивании файла этапа: {str(e)}"
        )


@router.get("/task/files/{file_id}/stream/{filename:path}")
async def stream_task_file(
        file_id: int,
        filename: str,
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query),
):
    file = db.query(TaskFile).filter(TaskFile.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="Файл задачи не найден")
    task = db.query(Task).filter(Task.id == file.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача файла не найдена")
    _ensure_task_read(db, current_user_id, task)

    effective_name = _effective_filename(file)
    if not is_video_filename(effective_name):
        raise HTTPException(status_code=400, detail="Файл не является видео")
    if filename != effective_name:
        raise HTTPException(status_code=404, detail="Имя файла не совпадает")
    if _is_raw_video(file):
        return await _chat_style_video_response(
            file, effective_name, request, download=False)
    return get_decompressed_file_response_with_range(
        request, file.filepath, effective_name, download=False)


@router.get("/stages/files/{file_id}/stream/{filename:path}")
async def stream_stage_file(
        file_id: int,
        filename: str,
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query),
):
    file = db.query(TaskStageFile).filter(TaskStageFile.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="Файл этапа не найден")
    task = _task_for_stage_file(db, file)
    _ensure_task_read(db, current_user_id, task)
    _ensure_stage_read(db, current_user_id, file)

    effective_name = _effective_filename(file)
    if not is_video_filename(effective_name):
        raise HTTPException(status_code=400, detail="Файл не является видео")
    if filename != effective_name:
        raise HTTPException(status_code=404, detail="Имя файла не совпадает")
    if _is_raw_video(file):
        return await _chat_style_video_response(
            file, effective_name, request, download=False)
    return get_decompressed_file_response_with_range(
        request, file.filepath, effective_name, download=False)


@router.get("/files/{file_id}/view")
async def view_file(
        file_id: int,
        is_stage: bool,
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)
):
    try:
        if is_stage:
            file = db.query(TaskStageFile).filter(TaskStageFile.id == file_id).first()
        else:
            file = db.query(TaskFile).filter(TaskFile.id == file_id).first()

        if not file:
            raise HTTPException(status_code=404, detail="Файл не найден")

        task = _task_for_stage_file(db, file) if is_stage else db.query(Task).filter(
            Task.id == file.task_id
        ).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача файла не найдена")
        _ensure_task_read(db, current_user_id, task)
        if is_stage:
            _ensure_stage_read(db, current_user_id, file)

        filename = _effective_filename(file)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if _is_raw_video(file):
            return await _chat_style_video_response(
                file, filename, request, download=False)
        if is_video_filename(filename):
            return get_decompressed_file_response_with_range(
                request, file.filepath, filename, download=False)
        if not any(content_type.startswith(t) for t in ['image/', 'text/', 'application/pdf']):
            # Документ показываем так же, как это делают мессенджеры: сервер
            # превращает его в PDF, и он открывается соседней вкладкой
            preview = office_preview_response(file.filepath, filename)
            if preview is not None:
                return preview
            raise HTTPException(
                status_code=415,
                detail="Этот тип файла нельзя показать в браузере — файл можно скачать"
            )

        return get_decompressed_file_response(file.filepath, filename, download=False, request=request)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при просмотре файла: {str(e)}"
        )



def _sheet_or_415(filepath: str, filename: str):
    """Разбор таблицы или отказ 415 — общий хвост для всех точек входа"""
    try:
        stored = resolve_stored_path(filepath)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    data = sheet_json(stored, filename)
    if data is None:
        raise HTTPException(status_code=415, detail="Этот файл не удалось прочитать как таблицу")
    return data

@router.get("/files/{file_id}/sheet")
def sheet_file(
        file_id: int,
        is_stage: bool,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)
):
    """Таблица настоящими ячейками: значения, ширины, объединения, оформление.

    Раньше xlsx показывался как PDF — картинкой таблицы. Здесь книга уходит
    клиенту данными, а сетку рисует браузер. Если разобрать не вышло, честно
    отвечаем отказом: клиент тогда откроет файл прежним способом.
    """
    file = db.query(TaskStageFile if is_stage else TaskFile).filter(
        (TaskStageFile if is_stage else TaskFile).id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="Файл не найден")
    task = _task_for_stage_file(db, file) if is_stage else db.query(Task).filter(
        Task.id == file.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача файла не найдена")
    _ensure_task_read(db, current_user_id, task)
    if is_stage:
        _ensure_stage_read(db, current_user_id, file)
    return _sheet_or_415(file.filepath, _effective_filename(file))


@router.get("/sheet_preview/{file_path:path}")
def sheet_preview_stored(
        file_path: str,
        current_user_id: int = Depends(get_current_user_id_from_header_or_query),
        db: Session = Depends(get_db),
        chat_db: Session = Depends(get_chat_db)
):
    """То же самое для вложений чата: у них нет номера в базе, только путь"""
    file_access.check_path(db, chat_db, current_user_id, file_path, "sheet_preview")
    try:
        resolve_stored_path(file_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    name = Path(file_path).name
    if name.endswith(".gz"):
        name = name[:-3]
    return _sheet_or_415(file_path, name)


@router.get("/task/files/{file_id}/info", response_model=FileInfo)
def get_task_file_info(
        file_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        file = db.query(TaskFile).filter(TaskFile.id == file_id).first()
        if not file:
            raise HTTPException(status_code=404, detail="Файл задачи не найден")

        task = db.query(Task).filter(Task.id == file.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        _ensure_task_read(db, current_user_id, task)

        return {
            "id": file.id,
            "filename": _effective_filename(file),
            "filepath": file.filepath,
            "size": file.size,
            "extension": file.extension,
            "task_id": file.task_id,
            "created_at": file.created_at.isoformat() if file.created_at else None,
            "uploaded_by_id": file.uploaded_by_id,
            "uploaded_by": _uploader_dict(file.uploaded_by),
            "content_type": mimetypes.guess_type(_effective_filename(file))[0] or "application/octet-stream"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении информации о файле: {str(e)}"
        )


@router.get("/stage/files/{file_id}/info", response_model=FileInfo)
def get_stage_file_info(
        file_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        file = db.query(TaskStageFile).filter(TaskStageFile.id == file_id).first()
        if not file:
            raise HTTPException(status_code=404, detail="Файл этапа не найден")

        stage = db.query(TaskStageDetails).filter(
            TaskStageDetails.id == file.stage_detail_id
        ).first()
        if not stage:
            raise HTTPException(status_code=404, detail="Этап не найден")

        task = db.query(Task).filter(Task.id == stage.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        _ensure_task_read(db, current_user_id, task)

        return {
            "id": file.id,
            "filename": _effective_filename(file),
            "filepath": file.filepath,
            "size": file.size,
            "extension": file.extension,
            "stage_detail_id": file.stage_detail_id,
            "task_id": stage.task_id,
            "created_at": file.created_at.isoformat() if file.created_at else None,
            "uploaded_by_id": file.uploaded_by_id,
            "uploaded_by": _uploader_dict(file.uploaded_by),
            "content_type": mimetypes.guess_type(_effective_filename(file))[0] or "application/octet-stream"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении информации о файле: {str(e)}"
        )


@router.delete("/files/task/{file_id}")
async def delete_task_file(
        file_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    quarantine = []
    try:
        file = db.query(TaskFile).filter(TaskFile.id == file_id).first()
        if not file:
            raise HTTPException(status_code=404, detail="Файл не найден")
        task = db.query(Task).filter(Task.id == file.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Задача файла не найдена")
        _ensure_task_access(db, current_user_id, task)
        if task_is_archived(task):
            raise HTTPException(status_code=409, detail="Задача в архиве — файлы изменить нельзя")
        # Журнал задачи: раньше удаление файла нигде не отмечалось —
        # человек открывал журнал и видел пустоту
        journal.note(db, task.id, "task_file",
                     "удалён «%s»" % _effective_filename(file), current_user_id)
        # Раньше файл задачи удалял любой её участник, включая рядового
        # исполнителя: чужую смету можно было стереть одним запросом
        if not (user_has_full_task_access(db, current_user_id)
                or user_is_department_head_for_task(db, current_user_id, task)
                or file.uploaded_by_id == current_user_id):
            raise HTTPException(status_code=403,
                                detail="Удалить файл задачи может тот, кто его прикрепил, или руководство")

        task_id = file.task_id
        quarantine = quarantine_files_for_deletion([file.filepath])

        db.delete(file)
        db.commit()
        purge_quarantined_files(quarantine)

        try:
            await manager.broadcast_to_task_participants(task_id, {
                "type": "task.file_deleted",
                "task_id": task_id,
                "file_id": file_id,
                "sender_id": current_user_id
            }, db)
            await announce_task_files(task_id, db)
        except Exception as e:
            logger.warning("Файлы задачи: рассылка об удалении не ушла: %s", e)

        return {"message": "Файл успешно удален"}
    except HTTPException:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise
    except Exception as e:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при удалении файла: {str(e)}"
        )


@router.delete("/files/stage/{file_id}")
async def delete_stage_file(
        file_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    quarantine = []
    try:
        file = db.query(TaskStageFile).filter(TaskStageFile.id == file_id).first()
        if not file:
            raise HTTPException(status_code=404, detail="Файл не найден")
        task = _task_for_stage_file(db, file)
        _ensure_task_access(db, current_user_id, task)
        stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == file.stage_detail_id).first()
        journal.note(db, task.id if task else None, "stage_file",
                     "удалён «%s» из этапа %s" % (_effective_filename(file),
                                                  getattr(stage, "stage_number", "—")),
                     current_user_id)
        if not (user_leads_stage(db, current_user_id, stage) or file.uploaded_by_id == current_user_id):
            raise HTTPException(status_code=403,
                                detail="Удалить файл этапа может тот, кто его прикрепил, или ведущий отдел")
        if task_is_archived(task):
            raise HTTPException(status_code=409, detail="Задача в архиве — файлы изменить нельзя")
        if stage is not None and stage.is_completed:
            raise HTTPException(status_code=409, detail="Этап завершён — возобновите его, чтобы изменить файлы")

        task_id = task.id
        stage_detail_id = file.stage_detail_id
        quarantine = quarantine_files_for_deletion([file.filepath])

        db.delete(file)
        db.commit()
        purge_quarantined_files(quarantine)

        try:
            await manager.broadcast_to_task_participants(task_id, {
                "type": "stage.file_deleted",
                "task_id": task_id,
                "stage_id": stage_detail_id,
                "file_id": file_id,
                "sender_id": current_user_id
            }, db)
            await announce_task_files(task_id, db)
        except Exception as e:
            logger.warning("Файлы этапа: рассылка об удалении не ушла: %s", e)

        return {"message": "Файл успешно удален"}
    except HTTPException:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise
    except Exception as e:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при удалении файла: {str(e)}"
        )


@router.get("/tasks/{task_id}/stages/{stage_id}/files", response_model=List[TaskStageFileOut])
def get_task_stage_files(
        task_id: int,
        stage_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        stage = db.query(TaskStageDetails).filter(
            TaskStageDetails.id == stage_id,
            TaskStageDetails.task_id == task_id
        ).first()

        if not stage:
            raise HTTPException(
                status_code=404,
                detail=f"Этап с ID {stage_id} не найден в задаче {task_id}"
            )

        files = db.query(TaskStageFile).filter(
            TaskStageFile.stage_detail_id == stage_id
        ).all()

        return files
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении файлов этапа: {str(e)}"
        )


@router.get("/file_storage/messenger/{path:path}")
async def get_messenger_file(path: str, response: Response, request: Request,
                             current_user_id: int = Depends(get_current_user_id_from_header_or_query),
                             db: Session = Depends(get_db),
                             chat_db: Session = Depends(get_chat_db)):
    """
    Специальный эндпоинт для обслуживания файлов мессенджера.
    Распаковывает GZIP файлы на лету.
    """
    try:
        file_access.check_path(db, chat_db, current_user_id, "messenger/" + path, "file_storage/messenger")
        # _storage_path режет выход за пределы хранилища через ../
        try:
            full_path = resolve_stored_path(str(Path("messenger") / path))
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректный путь к файлу")

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="Файл не найден")

        # Определяем имя файла из пути
        filename = Path(path).name
        # Убираем .gz расширение если есть
        if filename.endswith('.gz'):
            original_filename = filename[:-3]
        else:
            original_filename = filename

        from ..storage import storage_relative

        rel_path = str(storage_relative(full_path))

        # Для видео используем StaticFiles — корректная потоковая передача Range-запросов.
        # gzip для /file_storage отключён на уровне middleware (ConditionalGZipMiddleware),
        # поэтому никаких костылей с Content-Encoding: identity больше не нужно.
        if full_path.suffix in ['.webm', '.mp4']:
            resp = await video_static_app.get_response(rel_path, request.scope)
            resp.headers['Cache-Control'] = 'no-cache'
            if full_path.suffix == '.webm':
                resp.headers['Content-Type'] = 'video/webm'
            elif full_path.suffix == '.mp4':
                resp.headers['Content-Type'] = 'video/mp4'
            return resp

        # Используем существующую функцию для распаковки (для остальных файлов)
        return get_decompressed_file_response(rel_path, original_filename,
                                              download=False, request=request)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении файла мессенджера: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке файла: {str(e)}")


@router.get("/upload/progress/{upload_id}")
def upload_progress(upload_id: str, current_user_id: int = Depends(get_current_user_id)):
    """Ход обработки загрузки: по каждому файлу состояние и процент."""
    from .. import upload_queue as queue
    snap = queue.snapshot(upload_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Загрузка не найдена или уже завершена")
    return snap


@router.post("/files/access-token")
async def create_file_token(current_user_id: int = Depends(get_current_user_id)):
    """Короткий токен для адресов картинок и видео.

    Тег <img> нельзя снабдить заголовком Authorization, поэтому токен уходит
    в строку запроса. Этот живёт 20 минут и не открывает остальной API.
    """
    return issue_file_token(current_user_id)


@router.get("/file_preview/{file_path:path}")
def preview_stored_file(
        file_path: str,
        current_user_id: int = Depends(get_current_user_id_from_header_or_query),
        db: Session = Depends(get_db),
        chat_db: Session = Depends(get_chat_db)
):
    """PDF-предпросмотр документа из хранилища — для файлов чата.

    У вложений чата нет своего номера в базе, поэтому предпросмотр берётся
    по пути. Если преобразователь не установлен, честно отвечаем отказом —
    клиент тогда просто скачает файл.
    """
    file_access.check_path(db, chat_db, current_user_id, file_path, "file_preview")
    try:
        stored = resolve_stored_path(file_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    if not stored.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    name = Path(file_path).name
    if name.endswith(".gz"):
        name = name[:-3]
    made = office_preview_response(file_path, name)
    if made is None:
        raise HTTPException(status_code=415,
                            detail="Этот тип файла нельзя показать в браузере — файл можно скачать")
    return made


def _thumb_on_demand(thumb_path: Path) -> Optional[Path]:
    """<файл>.thumb.jpg, которой ещё нет: создать из исходного файла (обычного или .gz)."""
    from .. import media
    if not thumb_path.name.endswith(media.THUMB_SUFFIX):
        return None
    source = thumb_path.with_name(thumb_path.name[:-len(media.THUMB_SUFFIX)])
    for candidate in (source, source.with_name(source.name + ".gz")):
        if candidate.is_file():
            try:
                return media.ensure_thumbnail(candidate, source.name)
            except Exception:
                logger.warning("Миниатюра для «%s» не создана", source.name, exc_info=True)
                return None
    return None


@router.get("/file_storage/{file_path:path}")
async def serve_static_file(file_path: str, request: Request,
                            current_user_id: int = Depends(get_current_user_id_from_header_or_query),
                            db: Session = Depends(get_db),
                            chat_db: Session = Depends(get_chat_db)):
    """
    Кастомный обработчик статических файлов с поддержкой GZIP.
    """
    file_access.check_path(db, chat_db, current_user_id, file_path, "file_storage")
    # Без нормализации путь вида ../../etc/passwd читал бы что угодно
    try:
        full_path = resolve_stored_path(file_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")

    if not full_path.exists():
        # миниатюры (обложки видео) у старых файлов нет — делаем при первом запросе из исходного файла
        made = await asyncio.to_thread(_thumb_on_demand, full_path)
        if made is None:
            raise HTTPException(status_code=404, detail="Файл не найден")
        full_path = made

    # Миниатюры отдаются напрямую (кэшируются браузером на сутки)
    if full_path.name.endswith(".thumb.jpg"):
        return FileResponse(full_path, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    # Если файл с расширением .gz, распаковываем
    if full_path.suffix == '.gz':
        return get_decompressed_file_response(
            str(full_path.relative_to(Path(FILE_STORAGE_ROOT).resolve())),
            full_path.name[:-3],  # Убираем .gz
            download=False,
            request=request
        )
    # Путь в запросе был с .gz, а файл лежит без него (фото версии 2)
    if file_path.lower().endswith(".gz") and full_path.suffix.lower() != ".gz":
        from ..storage import storage_relative
        file_path = str(storage_relative(full_path))

    # Иначе отдаем как обычный файл. Для видео (webm/mp4) нужна поддержка Range-запросов.
    if full_path.suffix in ['.webm', '.mp4']:
        # gzip для /file_storage отключён на уровне middleware, поэтому Content-Encoding не трогаем.
        resp = await video_static_app.get_response(file_path, request.scope)
        resp.headers['Cache-Control'] = 'no-cache'
        if full_path.suffix == '.webm':
            resp.headers['Content-Type'] = 'video/webm'
        elif full_path.suffix == '.mp4':
            resp.headers['Content-Type'] = 'video/mp4'
        return resp

    # Тип определяем по белому списку: файл с расширением .html или .svg,
    # показанный встроенно, выполнялся бы как страница на нашем адресе
    return FileResponse(
        full_path,
        media_type=safe_content_type(full_path.name),
        headers={
            "Content-Disposition": disposition_for(full_path.name, download=False),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
