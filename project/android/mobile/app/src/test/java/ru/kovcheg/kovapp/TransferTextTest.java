package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import org.junit.Test;

import ru.kovcheg.kovapp.files.TransferText;

public class TransferTextTest {
    @Test public void titleEndings() {
        assertEquals("Отправка файла", TransferText.title(1));
        assertEquals("Отправка 3 файлов", TransferText.title(3));
        assertEquals("Отправка 11 файлов", TransferText.title(11));
        assertEquals("Отправка 21 файла", TransferText.title(21));
        assertEquals("Отправка файла", TransferText.title(0));
    }

    @Test public void percentText() {
        assertEquals("Подготовка…", TransferText.text(0));
        assertEquals("45% · можно свернуть приложение", TransferText.text(45));
        assertEquals("100% · можно свернуть приложение", TransferText.text(130));
    }
}
