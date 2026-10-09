package ru.kovcheg.kovapp.media;

import android.graphics.Bitmap;
import android.graphics.Matrix;
import android.graphics.SurfaceTexture;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
import android.opengl.GLES11Ext;
import android.opengl.GLES20;
import android.os.Handler;
import android.os.HandlerThread;
import android.view.Surface;

import java.io.IOException;
import java.nio.Buffer;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;

/**
 * Перерисовка кадра декодера в кадр кодировщика через OpenGL ES 2:
 * поворот, обрезка и масштаб задаются текстурными координатами углов
 * (FrameMapping) — геометрия та же, что у canvas в веб-версии.
 */
final class GlRenderer implements SurfaceTexture.OnFrameAvailableListener {
    private static final int EGL_RECORDABLE_ANDROID = 0x3142;
    private static final String VERTEX =
            "uniform mat4 uST;\n" +
            "attribute vec4 aPos;\n" +
            "attribute vec4 aTex;\n" +
            "varying vec2 vTex;\n" +
            "void main() { gl_Position = aPos; vTex = (uST * aTex).xy; }\n";
    private static final String FRAGMENT =
            "#extension GL_OES_EGL_image_external : require\n" +
            "precision mediump float;\n" +
            "varying vec2 vTex;\n" +
            "uniform samplerExternalOES sTex;\n" +
            "void main() { gl_FragColor = texture2D(sTex, vTex); }\n";
    private static final float[] POSITIONS = {-1, -1, 1, -1, -1, 1, 1, 1};

    private EGLDisplay display = EGL14.EGL_NO_DISPLAY;
    private EGLContext context = EGL14.EGL_NO_CONTEXT;
    private EGLSurface surface = EGL14.EGL_NO_SURFACE;
    private final int width, height;
    private int program, texId, aPos, aTex, uST;
    private SurfaceTexture texture;
    private Surface decoderSurface;
    private HandlerThread callbacks;
    private final Object lock = new Object();
    private boolean frameAvailable;
    private final float[] stMatrix = new float[16];
    private final FloatBuffer posBuf, texBuf;

    GlRenderer(Surface encoderInput, int width, int height) throws IOException {
        this.width = width;
        this.height = height;
        posBuf = ByteBuffer.allocateDirect(POSITIONS.length * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
        posBuf.put(POSITIONS);
        ((Buffer) posBuf).position(0);
        texBuf = ByteBuffer.allocateDirect(8 * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
        eglSetup(encoderInput);
        glSetup();
    }

    private void eglSetup(Surface out) throws IOException {
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        if (display == EGL14.EGL_NO_DISPLAY) throw new IOException("EGL: нет дисплея");
        int[] version = new int[2];
        if (!EGL14.eglInitialize(display, version, 0, version, 1)) throw new IOException("EGL: не инициализирован");
        int[] attribs = {
                EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT, EGL_RECORDABLE_ANDROID, 1, EGL14.EGL_NONE};
        EGLConfig[] configs = new EGLConfig[1];
        int[] num = new int[1];
        if (!EGL14.eglChooseConfig(display, attribs, 0, configs, 0, 1, num, 0) || num[0] == 0) throw new IOException("EGL: нет подходящей конфигурации");
        context = EGL14.eglCreateContext(display, configs[0], EGL14.EGL_NO_CONTEXT, new int[]{EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE}, 0);
        if (context == null || context == EGL14.EGL_NO_CONTEXT) throw new IOException("EGL: контекст не создан");
        surface = EGL14.eglCreateWindowSurface(display, configs[0], out, new int[]{EGL14.EGL_NONE}, 0);
        if (surface == null || surface == EGL14.EGL_NO_SURFACE) throw new IOException("EGL: поверхность кодировщика не создана");
        if (!EGL14.eglMakeCurrent(display, surface, surface, context)) throw new IOException("EGL: makeCurrent");
    }

    private static int shader(int type, String src) throws IOException {
        int s = GLES20.glCreateShader(type);
        GLES20.glShaderSource(s, src);
        GLES20.glCompileShader(s);
        int[] ok = new int[1];
        GLES20.glGetShaderiv(s, GLES20.GL_COMPILE_STATUS, ok, 0);
        if (ok[0] == 0) {
            String log = GLES20.glGetShaderInfoLog(s);
            GLES20.glDeleteShader(s);
            throw new IOException("шейдер не собрался: " + log);
        }
        return s;
    }

    private void glSetup() throws IOException {
        program = GLES20.glCreateProgram();
        GLES20.glAttachShader(program, shader(GLES20.GL_VERTEX_SHADER, VERTEX));
        GLES20.glAttachShader(program, shader(GLES20.GL_FRAGMENT_SHADER, FRAGMENT));
        GLES20.glLinkProgram(program);
        int[] ok = new int[1];
        GLES20.glGetProgramiv(program, GLES20.GL_LINK_STATUS, ok, 0);
        if (ok[0] == 0) throw new IOException("программа OpenGL не собралась");
        aPos = GLES20.glGetAttribLocation(program, "aPos");
        aTex = GLES20.glGetAttribLocation(program, "aTex");
        uST = GLES20.glGetUniformLocation(program, "uST");
        int[] tex = new int[1];
        GLES20.glGenTextures(1, tex, 0);
        texId = tex[0];
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, texId);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
        callbacks = new HandlerThread("kovapp-frames");
        callbacks.start();
        texture = new SurfaceTexture(texId);
        texture.setOnFrameAvailableListener(this, new Handler(callbacks.getLooper()));
        decoderSurface = new Surface(texture);
    }

    Surface decoderSurface() { return decoderSurface; }

    @Override public void onFrameAvailable(SurfaceTexture st) {
        synchronized (lock) {
            frameAvailable = true;
            lock.notifyAll();
        }
    }

    void awaitFrame(long timeoutMs) throws IOException {
        long end = System.currentTimeMillis() + timeoutMs;
        synchronized (lock) {
            while (!frameAvailable) {
                long left = end - System.currentTimeMillis();
                if (left <= 0) throw new IOException("кадр декодера не пришёл");
                try { lock.wait(left); } catch (InterruptedException e) { throw new IOException("прервано"); }
            }
            frameAvailable = false;
        }
        texture.updateTexImage();
        texture.getTransformMatrix(stMatrix);
    }

    void draw(float[] texCoords) {
        ((Buffer) texBuf).clear();
        texBuf.put(texCoords);
        ((Buffer) texBuf).position(0);
        GLES20.glViewport(0, 0, width, height);
        GLES20.glClearColor(0f, 0f, 0f, 1f);
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT);
        GLES20.glUseProgram(program);
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, texId);
        GLES20.glEnableVertexAttribArray(aPos);
        GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 8, posBuf);
        GLES20.glEnableVertexAttribArray(aTex);
        GLES20.glVertexAttribPointer(aTex, 2, GLES20.GL_FLOAT, false, 8, texBuf);
        GLES20.glUniformMatrix4fv(uST, 1, false, stMatrix, 0);
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);
        // без glFinish: кадр кодировщику синхронизирует eglSwapBuffers, снимок обложки — сам glReadPixels
    }

    void present(long ptsNs) {
        EGLExt.eglPresentationTimeANDROID(display, surface, ptsNs);
        EGL14.eglSwapBuffers(display, surface);
    }

    /** Текущий кадр (до present) — для обложки видео; null — снимок не удался (чёрный) */
    Bitmap readFrame() {
        ByteBuffer buf = ByteBuffer.allocateDirect(width * height * 4).order(ByteOrder.nativeOrder());
        GLES20.glReadPixels(0, 0, width, height, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, buf);
        // поверхность кодировщика на части устройств (и эмуляторов) не читается обратно — выходит сплошной чёрный
        if (CodecRanking.blank(buf, width * height)) return null;
        ((Buffer) buf).rewind();
        Bitmap raw = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        raw.copyPixelsFromBuffer(buf);
        Matrix flip = new Matrix();
        flip.preScale(1f, -1f);
        Bitmap out = Bitmap.createBitmap(raw, 0, 0, width, height, flip, false);
        if (out != raw) raw.recycle();
        return out;
    }

    void release() {
        if (decoderSurface != null) decoderSurface.release();
        if (texture != null) texture.release();
        if (callbacks != null) callbacks.quitSafely();
        if (display != EGL14.EGL_NO_DISPLAY) {
            EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
            if (surface != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(display, surface);
            if (context != EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(display, context);
            EGL14.eglReleaseThread();
            EGL14.eglTerminate(display);
        }
        display = EGL14.EGL_NO_DISPLAY;
        context = EGL14.EGL_NO_CONTEXT;
        surface = EGL14.EGL_NO_SURFACE;
    }
}
