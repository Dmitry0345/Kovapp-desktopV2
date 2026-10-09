"""Заглушки Android SDK / androidx / Firebase для проверочной компиляции без SDK.
Только члены, которые использует приложение, с реальными сигнатурами."""
import os, sys, textwrap

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), 'android-stubs')
S = {}

def cls(fq, body, header=None):
    S[fq] = (header, textwrap.dedent(body).strip('\n'))

# ── android.* ────────────────────────────────────────────────────────────
cls('android.Manifest', '''
public final class Manifest {
    public static final class permission {
        public static final String POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS";
        public static final String WRITE_EXTERNAL_STORAGE = "android.permission.WRITE_EXTERNAL_STORAGE";
    }
}''')
cls('android.annotation.SuppressLint', '''
@java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.CLASS)
public @interface SuppressLint { String[] value(); }''')
cls('android.annotation.TargetApi', '''
@java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.CLASS)
public @interface TargetApi { int value(); }''')
cls('android.content.Context', '''
public abstract class Context {
    public static final String NOTIFICATION_SERVICE = "notification", CONNECTIVITY_SERVICE = "connectivity",
        JOB_SCHEDULER_SERVICE = "jobscheduler", DOWNLOAD_SERVICE = "download", POWER_SERVICE = "power", ACTIVITY_SERVICE = "activity";
    public static final int MODE_PRIVATE = 0;
    public abstract Object getSystemService(String name);
    public final <T> T getSystemService(Class<T> c) { return null; }
    public abstract Context getApplicationContext();
    public abstract ContentResolver getContentResolver();
    public abstract SharedPreferences getSharedPreferences(String name, int mode);
    public abstract String getPackageName();
    public abstract java.io.File getCacheDir();
    public abstract android.content.res.AssetManager getAssets();
    public abstract android.content.pm.PackageManager getPackageManager();
    public abstract android.content.res.Resources getResources();
    public abstract int checkSelfPermission(String permission);
    public abstract ComponentName startForegroundService(Intent service);
    public abstract ComponentName startService(Intent service);
    public abstract boolean stopService(Intent service);
    public abstract void startActivity(Intent intent);
    public Intent registerReceiver(BroadcastReceiver receiver, IntentFilter filter) { return null; }
}''')
cls('android.content.ContextWrapper', '''
public class ContextWrapper extends Context {
    public Object getSystemService(String name) { return null; }
    public Context getApplicationContext() { return this; }
    public ContentResolver getContentResolver() { return null; }
    public SharedPreferences getSharedPreferences(String name, int mode) { return null; }
    public String getPackageName() { return ""; }
    public java.io.File getCacheDir() { return null; }
    public android.content.res.AssetManager getAssets() { return null; }
    public android.content.pm.PackageManager getPackageManager() { return null; }
    public android.content.res.Resources getResources() { return null; }
    public int checkSelfPermission(String permission) { return 0; }
    public ComponentName startForegroundService(Intent service) { return null; }
    public ComponentName startService(Intent service) { return null; }
    public boolean stopService(Intent service) { return false; }
    public void startActivity(Intent intent) { }
}''')
cls('android.app.Application', '''
public class Application extends android.content.ContextWrapper { public void onCreate() { } }''')
cls('android.app.Activity', '''
public class Activity extends android.content.ContextWrapper {
    public static final int RESULT_OK = -1;
    protected void onCreate(android.os.Bundle savedInstanceState) { }
    protected void onResume() { }
    protected void onPause() { }
    protected void onDestroy() { }
    protected void onNewIntent(android.content.Intent intent) { }
    protected void onActivityResult(int requestCode, int resultCode, android.content.Intent data) { }
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) { }
    @Deprecated public void onBackPressed() { }
    public void setContentView(android.view.View view) { }
    public android.view.Window getWindow() { return null; }
    public final void runOnUiThread(Runnable action) { }
    public void startActivityForResult(android.content.Intent intent, int requestCode) { }
    public final void requestPermissions(String[] permissions, int requestCode) { }
    public boolean shouldShowRequestPermissionRationale(String permission) { return false; }
    public android.content.Intent getIntent() { return null; }
    public void setIntent(android.content.Intent newIntent) { }
    public boolean moveTaskToBack(boolean nonRoot) { return false; }
    public void finish() { }
    public android.window.OnBackInvokedDispatcher getOnBackInvokedDispatcher() { return null; }
    public void setRequestedOrientation(int o) { } public int getRequestedOrientation() { return -1; }
}''')
cls('android.app.ActivityManager', '''
public class ActivityManager {
    public static class MemoryInfo { public long totalMem; public long availMem; }
    public void getMemoryInfo(MemoryInfo outInfo) { }
}''')
cls('android.app.Service', '''
public abstract class Service extends android.content.ContextWrapper {
    public static final int START_STICKY = 1; public static final int START_NOT_STICKY = 2;
    public void onCreate() { }
    public int onStartCommand(android.content.Intent intent, int flags, int startId) { return 0; }
    public void onDestroy() { }
    public abstract android.os.IBinder onBind(android.content.Intent intent);
    public final void startForeground(int id, Notification notification) { }
    public final void startForeground(int id, Notification notification, int foregroundServiceType) { }
    public final void stopForeground(boolean removeNotification) { }
    public void onTaskRemoved(android.content.Intent rootIntent) { }
    public final void stopSelf() { }
}''')
cls('android.app.job.JobService', '''
public abstract class JobService extends android.app.Service {
    public final android.os.IBinder onBind(android.content.Intent intent) { return null; }
    public abstract boolean onStartJob(JobParameters params);
    public abstract boolean onStopJob(JobParameters params);
    public final void jobFinished(JobParameters params, boolean wantsReschedule) { }
}''')
cls('android.app.job.JobParameters', 'public class JobParameters { }')
cls('android.app.job.JobInfo', '''
public class JobInfo {
    public static final int NETWORK_TYPE_ANY = 1;
    public static final class Builder {
        public Builder(int jobId, android.content.ComponentName jobService) { }
        public Builder setRequiredNetworkType(int networkType) { return this; }
        public Builder setPeriodic(long intervalMillis) { return this; }
        public Builder setPersisted(boolean isPersisted) { return this; }
        public JobInfo build() { return null; }
    }
}''')
cls('android.app.job.JobScheduler', '''
public abstract class JobScheduler {
    public abstract int schedule(JobInfo job);
    public abstract void cancel(int jobId);
}''')
cls('android.app.Notification', '''
public class Notification {
    public static final int VISIBILITY_PRIVATE = 0, PRIORITY_DEFAULT = 0, PRIORITY_HIGH = 1, DEFAULT_ALL = -1;
    public static final String CATEGORY_MESSAGE = "msg", CATEGORY_EVENT = "event";
    public abstract static class Style { }
    public static class BigTextStyle extends Style { public BigTextStyle bigText(CharSequence cs) { return this; } }
    public static class Builder {
        public Builder(android.content.Context context, String channelId) { }
        @Deprecated public Builder(android.content.Context context) { }
        public Builder setSmallIcon(int icon) { return this; }
        public Builder setColor(int argb) { return this; }
        public Builder setContentTitle(CharSequence title) { return this; }
        public Builder setContentText(CharSequence text) { return this; }
        public Builder setStyle(Style style) { return this; }
        public Builder setAutoCancel(boolean autoCancel) { return this; }
        public Builder setOnlyAlertOnce(boolean onlyAlertOnce) { return this; }
        public Builder setShowWhen(boolean show) { return this; }
        public Builder setWhen(long when) { return this; }
        public Builder setCategory(String category) { return this; }
        public Builder setVisibility(int visibility) { return this; }
        public Builder setPublicVersion(Notification n) { return this; }
        public Builder setContentIntent(PendingIntent intent) { return this; }
        public Builder setOngoing(boolean ongoing) { return this; }
        public Builder setProgress(int max, int progress, boolean indeterminate) { return this; }
        public Builder addAction(int icon, CharSequence title, PendingIntent intent) { return this; }
        @Deprecated public Builder setPriority(int pri) { return this; }
        @Deprecated public Builder setDefaults(int defaults) { return this; }
        public Notification build() { return null; }
    }
}''')
cls('android.app.NotificationChannel', '''
public final class NotificationChannel {
    public NotificationChannel(String id, CharSequence name, int importance) { }
    public void setDescription(String description) { }
    public void setLockscreenVisibility(int visibility) { }
    public void setShowBadge(boolean showBadge) { }
    public int getImportance() { return 3; }
}''')
cls('android.app.NotificationManager', '''
public class NotificationManager {
    public static final int IMPORTANCE_NONE = 0, IMPORTANCE_MIN = 1, IMPORTANCE_LOW = 2, IMPORTANCE_DEFAULT = 3, IMPORTANCE_HIGH = 4;
    public void createNotificationChannels(java.util.List<NotificationChannel> channels) { }
    public NotificationChannel getNotificationChannel(String channelId) { return null; }   // API 26
    public boolean areNotificationsEnabled() { return true; }
    public void notify(String tag, int id, Notification notification) { }
    public void notify(int id, Notification notification) { }
    public void cancel(String tag, int id) { }
    public void cancelAll() { }
}''')
cls('android.app.PendingIntent', '''
public final class PendingIntent {
    public static final int FLAG_UPDATE_CURRENT = 1 << 27, FLAG_IMMUTABLE = 1 << 26;
    public static PendingIntent getActivity(android.content.Context context, int requestCode, android.content.Intent intent, int flags) { return null; }
    public static PendingIntent getService(android.content.Context context, int requestCode, android.content.Intent intent, int flags) { return null; }
}''')
cls('android.app.DownloadManager', '''
public class DownloadManager {
    public static class Request {
        public static final int VISIBILITY_VISIBLE_NOTIFY_COMPLETED = 1;
        public Request(android.net.Uri uri) { }
        public Request setTitle(CharSequence title) { return this; }
        public Request setDescription(CharSequence d) { return this; }
        public Request setMimeType(String mimeType) { return this; }
        public Request addRequestHeader(String header, String value) { return this; }
        public Request setNotificationVisibility(int visibility) { return this; }
        public Request setDestinationInExternalPublicDir(String dirType, String subPath) { return this; }
    }
    public long enqueue(Request request) { return 0; }
    @Deprecated public long addCompletedDownload(String title, String description, boolean isMediaScannerScannable, String mimeType, String path, long length, boolean showNotification) { return 0; }
}''')
cls('android.content.pm.ActivityInfo', 'public class ActivityInfo { public static final int SCREEN_ORIENTATION_FULL_SENSOR = 10; public static final int SCREEN_ORIENTATION_UNSPECIFIED = -1; }')
cls('android.content.IntentFilter', 'public class IntentFilter { public IntentFilter(String action) { } }')
cls('android.content.BroadcastReceiver', '''
public abstract class BroadcastReceiver { public abstract void onReceive(Context context, Intent intent); }''')
cls('android.content.ActivityNotFoundException', 'public class ActivityNotFoundException extends RuntimeException { }')
cls('android.content.ClipData', '''
public class ClipData {
    public static class Item { public android.net.Uri getUri() { return null; } }
    public static ClipData newRawUri(CharSequence label, android.net.Uri uri) { return null; }
    public int getItemCount() { return 0; }
    public Item getItemAt(int index) { return null; }
}''')
cls('android.content.ComponentName', '''
public final class ComponentName { public ComponentName(Context pkg, Class<?> cls) { } }''')
cls('android.content.ContentProvider', '''
public abstract class ContentProvider {
    public final Context getContext() { return null; }
    public abstract boolean onCreate();
    public abstract android.database.Cursor query(android.net.Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder);
    public abstract String getType(android.net.Uri uri);
    public abstract android.net.Uri insert(android.net.Uri uri, ContentValues values);
    public abstract int delete(android.net.Uri uri, String selection, String[] selectionArgs);
    public abstract int update(android.net.Uri uri, ContentValues values, String selection, String[] selectionArgs);
    public android.os.ParcelFileDescriptor openFile(android.net.Uri uri, String mode) throws java.io.FileNotFoundException { return null; }
}''')
cls('android.content.ContentResolver', '''
public abstract class ContentResolver {
    public final android.graphics.Bitmap loadThumbnail(android.net.Uri uri, android.util.Size size, android.os.CancellationSignal s) throws java.io.IOException { return null; }
    public final android.database.Cursor query(android.net.Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) { return null; }
    public final android.os.ParcelFileDescriptor openFileDescriptor(android.net.Uri uri, String mode) throws java.io.FileNotFoundException { return null; }
    public final java.io.InputStream openInputStream(android.net.Uri uri) throws java.io.FileNotFoundException { return null; }
    public final java.io.OutputStream openOutputStream(android.net.Uri uri) throws java.io.FileNotFoundException { return null; }
    public final String getType(android.net.Uri url) { return null; }
    public final android.net.Uri insert(android.net.Uri url, ContentValues values) { return null; }
    public final int update(android.net.Uri uri, ContentValues values, String where, String[] selectionArgs) { return 0; }
    public final int delete(android.net.Uri url, String where, String[] selectionArgs) { return 0; }
}''')
cls('android.content.ContentValues', '''
public final class ContentValues {
    public void put(String key, String value) { }
    public void put(String key, Integer value) { }
}''')
cls('android.content.Intent', '''
public class Intent implements android.os.Parcelable {
    public static final String ACTION_GET_CONTENT = "g", CATEGORY_OPENABLE = "o", EXTRA_MIME_TYPES = "m", EXTRA_ALLOW_MULTIPLE = "a",
        EXTRA_INITIAL_INTENTS = "i", ACTION_VIEW = "v", ACTION_DIAL = "d", CATEGORY_BROWSABLE = "b",
        ACTION_BOOT_COMPLETED = "boot", ACTION_MY_PACKAGE_REPLACED = "repl";
    public static final int FLAG_GRANT_READ_URI_PERMISSION = 1, FLAG_GRANT_WRITE_URI_PERMISSION = 2,
        FLAG_ACTIVITY_NEW_TASK = 0x10000000, FLAG_ACTIVITY_SINGLE_TOP = 0x20000000, FLAG_ACTIVITY_CLEAR_TOP = 0x04000000;
    public Intent(String action) { }
    public Intent(String action, android.net.Uri uri) { }
    public Intent(Context packageContext, Class<?> cls) { }
    public static Intent createChooser(Intent target, CharSequence title) { return null; }
    public Intent setAction(String action) { return this; }
    public Intent putExtra(String name, String value) { return this; }
    public Intent putExtra(String name, boolean value) { return this; }
    public Intent putExtra(String name, String[] value) { return this; }
    public Intent putExtra(String name, android.os.Parcelable value) { return this; }
    public Intent putExtra(String name, android.os.Parcelable[] value) { return this; }
    public Intent addFlags(int flags) { return this; }
    public Intent addCategory(String category) { return this; }
    public Intent setType(String type) { return this; }
    public Intent setDataAndType(android.net.Uri data, String type) { return this; }
    public void setClipData(ClipData clip) { }
    public ClipData getClipData() { return null; }
    public android.net.Uri getData() { return null; }
    public String getAction() { return null; }
    public String getStringExtra(String name) { return null; }
    public ComponentName resolveActivity(android.content.pm.PackageManager pm) { return null; }
    public int getIntExtra(String name, int defaultValue) { return defaultValue; }
}''')
cls('android.content.SharedPreferences', '''
public interface SharedPreferences {
    interface Editor {
        Editor putString(String key, String value);
        Editor putBoolean(String key, boolean value);
        Editor putLong(String key, long value);
        Editor remove(String key);
        void apply();
    }
    String getString(String key, String defValue);
    boolean getBoolean(String key, boolean defValue);
    long getLong(String key, long defValue);
    Editor edit();
}''')
cls('android.content.pm.PackageInfo', 'public class PackageInfo { public String packageName; public String versionName; }')
cls('android.content.pm.PackageManager', 'public abstract class PackageManager { public static final int PERMISSION_GRANTED = 0; }')
cls('android.content.pm.ServiceInfo', 'public class ServiceInfo { public static final int FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING = 512; public static final int FOREGROUND_SERVICE_TYPE_DATA_SYNC = 1; }')
cls('android.content.res.AssetManager', '''
public final class AssetManager { public java.io.InputStream open(String fileName) throws java.io.IOException { return null; } }''')
cls('android.content.res.Resources', 'public class Resources { public android.util.DisplayMetrics getDisplayMetrics() { return null; } }')
cls('android.util.DisplayMetrics', 'public class DisplayMetrics { public float density; }')
cls('android.database.Cursor', '''
public interface Cursor extends java.io.Closeable {
    boolean moveToFirst();
    boolean moveToNext();
    int getColumnIndex(String columnName);
    boolean isNull(int columnIndex);
    String getString(int columnIndex);
    long getLong(int columnIndex);
    void close();
}''')
cls('android.database.MatrixCursor', '''
public class MatrixCursor implements Cursor {
    public MatrixCursor(String[] columnNames, int initialCapacity) { }
    public void addRow(Object[] columnValues) { }
    public boolean moveToNext() { return false; } public boolean moveToFirst() { return false; }
    public int getColumnIndex(String columnName) { return -1; }
    public boolean isNull(int i) { return true; }
    public String getString(int i) { return null; }
    public long getLong(int i) { return 0; }
    public void close() { }
}''')
# graphics
cls('android.graphics.Bitmap', '''
public final class Bitmap {
    public enum Config { ARGB_8888 }
    public enum CompressFormat { JPEG, PNG, @Deprecated WEBP, WEBP_LOSSY, WEBP_LOSSLESS }
    public int getWidth() { return 0; }
    public int getHeight() { return 0; }
    public void recycle() { }
    public boolean compress(CompressFormat format, int quality, java.io.OutputStream stream) { return true; }
    public void copyPixelsFromBuffer(java.nio.Buffer src) { }
    public static Bitmap createBitmap(int width, int height, Config config) { return null; }
    public static Bitmap createBitmap(Bitmap source, int x, int y, int width, int height, Matrix m, boolean filter) { return null; }
    public static Bitmap createScaledBitmap(Bitmap src, int dstWidth, int dstHeight, boolean filter) { return null; }
}''')
cls('android.graphics.BitmapFactory', '''
public class BitmapFactory {
    public static class Options { public boolean inJustDecodeBounds; public int outWidth, outHeight, inSampleSize; }
    public static Bitmap decodeStream(java.io.InputStream is, Rect outPadding, Options opts) { return null; }
}''')
cls('android.graphics.Canvas', '''
public class Canvas { public Canvas(Bitmap bitmap) { } public void drawBitmap(Bitmap bitmap, Rect src, Rect dst, Paint paint) { } }''')
cls('android.graphics.Color', '''
public class Color {
    public static final int WHITE = 0xFFFFFFFF, BLACK = 0xFF000000, TRANSPARENT = 0;
    public static int rgb(int red, int green, int blue) { return 0; }
    public static int red(int color) { return 0; }
    public static int green(int color) { return 0; }
    public static int blue(int color) { return 0; }
}''')
cls('android.graphics.ImageDecoder', '''
public final class ImageDecoder implements AutoCloseable {
    public static final int ALLOCATOR_SOFTWARE = 1;
    public abstract static class Source { }
    public static class ImageInfo { public android.util.Size getSize() { return null; } }
    public interface OnHeaderDecodedListener { void onHeaderDecoded(ImageDecoder decoder, ImageInfo info, Source source); }
    public static Source createSource(android.content.ContentResolver cr, android.net.Uri uri) { return null; }
    public static Bitmap decodeBitmap(Source src, OnHeaderDecodedListener listener) throws java.io.IOException { return null; }
    public void setTargetSize(int width, int height) { }
    public void setAllocator(int allocator) { }
    public void close() { }
}''')
cls('android.graphics.Matrix', '''
public class Matrix {
    public boolean preScale(float sx, float sy) { return true; }
    public void setScale(float sx, float sy) { }
    public void setRotate(float degrees) { }
    public boolean postScale(float sx, float sy) { return true; }
    public boolean isIdentity() { return true; }
}''')
cls('android.graphics.Paint', '''
public class Paint { public static final int ANTI_ALIAS_FLAG = 1, FILTER_BITMAP_FLAG = 2; public Paint(int flags) { } }''')
cls('android.graphics.Rect', 'public final class Rect { public Rect(int left, int top, int right, int bottom) { } }')
cls('android.graphics.Insets', 'public final class Insets { public final int left = 0, top = 0, right = 0, bottom = 0; }')
cls('android.graphics.SurfaceTexture', '''
public class SurfaceTexture {
    public interface OnFrameAvailableListener { void onFrameAvailable(SurfaceTexture surfaceTexture); }
    public SurfaceTexture(int texName) { }
    public void setOnFrameAvailableListener(OnFrameAvailableListener listener, android.os.Handler handler) { }
    public void updateTexImage() { }
    public void getTransformMatrix(float[] mtx) { }
    public void release() { }
}''')
cls('android.util.Size', 'public final class Size { public Size(int w, int h) { } public int getWidth() { return 0; } public int getHeight() { return 0; } }')
cls('android.util.Range', '''
public final class Range<T extends Comparable<? super T>> { public T getLower() { return null; } public T getUpper() { return null; } }''')
cls('android.util.Log', '''
public final class Log {
    public static int d(String tag, String msg) { return 0; }
    public static int i(String tag, String msg) { return 0; }
    public static int w(String tag, String msg) { return 0; }
    public static int e(String tag, String msg) { return 0; }
}''')
# media
cls('android.media.ExifInterface', '''
public class ExifInterface {
    public static final String TAG_ORIENTATION = "Orientation";
    public static final int ORIENTATION_NORMAL = 1, ORIENTATION_FLIP_HORIZONTAL = 2, ORIENTATION_ROTATE_180 = 3, ORIENTATION_FLIP_VERTICAL = 4,
        ORIENTATION_TRANSPOSE = 5, ORIENTATION_ROTATE_90 = 6, ORIENTATION_TRANSVERSE = 7, ORIENTATION_ROTATE_270 = 8;
    public ExifInterface(java.io.InputStream inputStream) throws java.io.IOException { }
    public int getAttributeInt(String tag, int defaultValue) { return defaultValue; }
}''')
cls('android.media.MediaCrypto', 'public final class MediaCrypto { }')
cls('android.media.MediaDescrambler', 'public final class MediaDescrambler { }')
cls('android.media.MediaCodec', '''
public final class MediaCodec {
    public static final int BUFFER_FLAG_KEY_FRAME = 1, BUFFER_FLAG_CODEC_CONFIG = 2, BUFFER_FLAG_END_OF_STREAM = 4,
        INFO_TRY_AGAIN_LATER = -1, INFO_OUTPUT_FORMAT_CHANGED = -2, CONFIGURE_FLAG_ENCODE = 1;
    public static final String PARAMETER_KEY_REQUEST_SYNC_FRAME = "request-sync";
    public static final class BufferInfo { public int offset, size, flags; public long presentationTimeUs; }
    public static MediaCodec createByCodecName(String name) throws java.io.IOException { return null; }
    public void configure(MediaFormat format, android.view.Surface surface, MediaCrypto crypto, int flags) { }
    public void configure(MediaFormat format, android.view.Surface surface, int flags, MediaDescrambler descrambler) { }
    public android.view.Surface createInputSurface() { return null; }
    public void start() { }
    public void stop() { }
    public void reset() { }
    public void release() { }
    public int dequeueInputBuffer(long timeoutUs) { return 0; }
    public java.nio.ByteBuffer getInputBuffer(int index) { return null; }
    public void queueInputBuffer(int index, int offset, int size, long presentationTimeUs, int flags) { }
    public int dequeueOutputBuffer(BufferInfo info, long timeoutUs) { return 0; }
    public java.nio.ByteBuffer getOutputBuffer(int index) { return null; }
    public void releaseOutputBuffer(int index, boolean render) { }
    public MediaFormat getOutputFormat() { return null; }
    public void signalEndOfInputStream() { }
    public void setParameters(android.os.Bundle params) { }
}''')
cls('android.media.MediaCodecInfo', '''
public final class MediaCodecInfo {
    public String getName() { return null; }
    public boolean isEncoder() { return false; }
    public boolean isHardwareAccelerated() { return false; }
    public String[] getSupportedTypes() { return null; }
    public CodecCapabilities getCapabilitiesForType(String type) { return null; }
    public static final class CodecCapabilities {
        public static final int COLOR_FormatSurface = 0x7F000789;
        public CodecProfileLevel[] profileLevels;
        public VideoCapabilities getVideoCapabilities() { return null; }
        public EncoderCapabilities getEncoderCapabilities() { return null; }
    }
    public static final class CodecProfileLevel {
        public static final int AVCProfileBaseline = 1, AVCProfileMain = 2, AVCProfileHigh = 8,
            AVCLevel31 = 0x200, AVCLevel4 = 0x800, AVCLevel5 = 0x4000, AVCLevel51 = 0x8000;
        public int profile, level;
    }
    public static final class VideoCapabilities {
        public boolean isSizeSupported(int width, int height) { return false; }
        public android.util.Range<Integer> getBitrateRange() { return null; }
        public android.util.Range<Double> getAchievableFrameRatesFor(int width, int height) { return null; }
        public java.util.List<PerformancePoint> getSupportedPerformancePoints() { return null; }
        public static final class PerformancePoint {
            public PerformancePoint(int width, int height, int frameRate) { }
            public boolean covers(PerformancePoint other) { return false; }
        }
    }
    public static final class EncoderCapabilities {
        public static final int BITRATE_MODE_VBR = 1;
        public boolean isBitrateModeSupported(int mode) { return false; }
    }
}''')
cls('android.media.MediaCodecList', '''
public final class MediaCodecList {
    public static final int REGULAR_CODECS = 0;
    public MediaCodecList(int kind) { }
    public MediaCodecInfo[] getCodecInfos() { return null; }
    public String findDecoderForFormat(MediaFormat format) { return null; }
}''')
cls('android.media.MediaFormat', '''
public final class MediaFormat {
    public static final String KEY_MAX_INPUT_SIZE = "max-input-size", KEY_COLOR_FORMAT = "color-format", KEY_BIT_RATE = "bitrate",
        KEY_FRAME_RATE = "frame-rate", KEY_I_FRAME_INTERVAL = "i-frame-interval", KEY_BITRATE_MODE = "bitrate-mode",
        KEY_PROFILE = "profile", KEY_LEVEL = "level", KEY_OPERATING_RATE = "operating-rate", KEY_PRIORITY = "priority",
        MIMETYPE_VIDEO_AVC = "video/avc", MIMETYPE_VIDEO_HEVC = "video/hevc", MIMETYPE_VIDEO_VP9 = "video/x-vnd.on2.vp9", MIMETYPE_VIDEO_VP8 = "video/x-vnd.on2.vp8";
    public static MediaFormat createVideoFormat(String mime, int width, int height) { return null; }
    public void setByteBuffer(String name, java.nio.ByteBuffer bytes) { }
    public void setInteger(String name, int value) { }
    public boolean containsKey(String name) { return false; }
    public java.nio.ByteBuffer getByteBuffer(String name) { return null; }
}''')
# net
cls('android.net.Uri', '''
public abstract class Uri implements android.os.Parcelable, Comparable<Uri> {
    public static Uri parse(String uriString) { return null; }
    public static String encode(String s) { return s; }
    public abstract String getScheme();
    public abstract String getLastPathSegment();
    public abstract java.util.List<String> getPathSegments();
    public static final class Builder {
        public Builder scheme(String scheme) { return this; }
        public Builder authority(String authority) { return this; }
        public Builder appendPath(String newSegment) { return this; }
        public Uri build() { return null; }
    }
    public int compareTo(Uri other) { return 0; }
}''')
cls('android.net.Network', 'public class Network { }')
cls('android.net.NetworkCapabilities', '''
public final class NetworkCapabilities { public static final int NET_CAPABILITY_INTERNET = 12; public boolean hasCapability(int capability) { return false; } }''')
cls('android.net.NetworkRequest', '''
public class NetworkRequest {
    public static class Builder { public Builder addCapability(int capability) { return this; } public NetworkRequest build() { return null; } }
}''')
cls('android.net.ConnectivityManager', '''
public class ConnectivityManager {
    public static class NetworkCallback { public void onAvailable(Network network) { } }
    public Network getActiveNetwork() { return null; }
    public NetworkCapabilities getNetworkCapabilities(Network network) { return null; }
    public void registerNetworkCallback(NetworkRequest request, NetworkCallback networkCallback) { }
    public void unregisterNetworkCallback(NetworkCallback networkCallback) { }
}''')
cls('android.net.http.SslError', 'public class SslError { }')
# opengl
cls('android.opengl.EGLConfig', 'public class EGLConfig { }')
cls('android.opengl.EGLContext', 'public class EGLContext { }')
cls('android.opengl.EGLDisplay', 'public class EGLDisplay { }')
cls('android.opengl.EGLSurface', 'public class EGLSurface { }')
cls('android.opengl.EGL14', '''
public class EGL14 {
    public static final int EGL_DEFAULT_DISPLAY = 0, EGL_RED_SIZE = 0x3024, EGL_GREEN_SIZE = 0x3023, EGL_BLUE_SIZE = 0x3022, EGL_ALPHA_SIZE = 0x3021,
        EGL_RENDERABLE_TYPE = 0x3040, EGL_OPENGL_ES2_BIT = 4, EGL_NONE = 0x3038, EGL_CONTEXT_CLIENT_VERSION = 0x3098;
    public static final EGLDisplay EGL_NO_DISPLAY = null;
    public static final EGLContext EGL_NO_CONTEXT = null;
    public static final EGLSurface EGL_NO_SURFACE = null;
    public static EGLDisplay eglGetDisplay(int display_id) { return null; }
    public static boolean eglInitialize(EGLDisplay dpy, int[] major, int majorOffset, int[] minor, int minorOffset) { return false; }
    public static boolean eglChooseConfig(EGLDisplay dpy, int[] attrib_list, int attrib_listOffset, EGLConfig[] configs, int configsOffset, int config_size, int[] num_config, int num_configOffset) { return false; }
    public static EGLContext eglCreateContext(EGLDisplay dpy, EGLConfig config, EGLContext share_context, int[] attrib_list, int offset) { return null; }
    public static EGLSurface eglCreateWindowSurface(EGLDisplay dpy, EGLConfig config, Object win, int[] attrib_list, int offset) { return null; }
    public static boolean eglMakeCurrent(EGLDisplay dpy, EGLSurface draw, EGLSurface read, EGLContext ctx) { return false; }
    public static boolean eglSwapBuffers(EGLDisplay dpy, EGLSurface surface) { return false; }
    public static boolean eglDestroySurface(EGLDisplay dpy, EGLSurface surface) { return false; }
    public static boolean eglDestroyContext(EGLDisplay dpy, EGLContext ctx) { return false; }
    public static boolean eglReleaseThread() { return false; }
    public static boolean eglTerminate(EGLDisplay dpy) { return false; }
}''')
cls('android.opengl.EGLExt', '''
public class EGLExt { public static boolean eglPresentationTimeANDROID(EGLDisplay dpy, EGLSurface sur, long time) { return false; } }''')
cls('android.opengl.GLES11Ext', 'public class GLES11Ext { public static final int GL_TEXTURE_EXTERNAL_OES = 0x8D65; }')
cls('android.opengl.GLES20', '''
public class GLES20 {
    public static final int GL_VERTEX_SHADER = 0x8B31, GL_FRAGMENT_SHADER = 0x8B30, GL_COMPILE_STATUS = 0x8B81, GL_LINK_STATUS = 0x8B82,
        GL_TEXTURE_MIN_FILTER = 0x2801, GL_TEXTURE_MAG_FILTER = 0x2800, GL_TEXTURE_WRAP_S = 0x2802, GL_TEXTURE_WRAP_T = 0x2803,
        GL_LINEAR = 0x2601, GL_CLAMP_TO_EDGE = 0x812F, GL_COLOR_BUFFER_BIT = 0x4000, GL_TEXTURE0 = 0x84C0, GL_FLOAT = 0x1406,
        GL_TRIANGLE_STRIP = 5, GL_RGBA = 0x1908, GL_UNSIGNED_BYTE = 0x1401;
    public static int glCreateShader(int type) { return 0; }
    public static void glShaderSource(int shader, String string) { }
    public static void glCompileShader(int shader) { }
    public static void glGetShaderiv(int shader, int pname, int[] params, int offset) { }
    public static String glGetShaderInfoLog(int shader) { return ""; }
    public static void glDeleteShader(int shader) { }
    public static int glCreateProgram() { return 0; }
    public static void glAttachShader(int program, int shader) { }
    public static void glLinkProgram(int program) { }
    public static void glGetProgramiv(int program, int pname, int[] params, int offset) { }
    public static int glGetAttribLocation(int program, String name) { return 0; }
    public static int glGetUniformLocation(int program, String name) { return 0; }
    public static void glGenTextures(int n, int[] textures, int offset) { }
    public static void glBindTexture(int target, int texture) { }
    public static void glTexParameteri(int target, int pname, int param) { }
    public static void glViewport(int x, int y, int width, int height) { }
    public static void glClearColor(float red, float green, float blue, float alpha) { }
    public static void glClear(int mask) { }
    public static void glUseProgram(int program) { }
    public static void glActiveTexture(int texture) { }
    public static void glEnableVertexAttribArray(int index) { }
    public static void glVertexAttribPointer(int indx, int size, int type, boolean normalized, int stride, java.nio.Buffer ptr) { }
    public static void glUniformMatrix4fv(int location, int count, boolean transpose, float[] value, int offset) { }
    public static void glDrawArrays(int mode, int first, int count) { }
    public static void glFinish() { }
    public static void glReadPixels(int x, int y, int width, int height, int format, int type, java.nio.Buffer pixels) { }
}''')
# os
cls('android.os.Parcelable', 'public interface Parcelable { }')
cls('android.os.Build', '''
public class Build {
    public static final String MANUFACTURER = "", MODEL = "", HARDWARE = "", BOARD = "", SOC_MANUFACTURER = "";
    public static class VERSION { public static final int SDK_INT = Integer.parseInt("24"); }
}''')
cls('android.os.Bundle', 'public final class Bundle { public void putInt(String key, int value) { } }')
cls('android.os.Environment', '''
public class Environment {
    public static String DIRECTORY_DOWNLOADS = "Download";
    @Deprecated public static java.io.File getExternalStoragePublicDirectory(String type) { return null; }
}''')
cls('android.os.Looper', 'public final class Looper { public static Looper getMainLooper() { return null; } }')
cls('android.os.Handler', 'public class Handler { public Handler(Looper looper) { } public final boolean post(Runnable r) { return true; } }')
cls('android.os.HandlerThread', '''
public class HandlerThread extends Thread { public HandlerThread(String name) { } public Looper getLooper() { return null; } public boolean quitSafely() { return true; } }''')
cls('android.os.IBinder', 'public interface IBinder { }')
cls('android.os.Message', 'public final class Message { public Object obj; public void sendToTarget() { } }')
cls('android.os.ParcelFileDescriptor', '''
public class ParcelFileDescriptor implements java.io.Closeable {
    public static ParcelFileDescriptor open(java.io.File file, int mode) throws java.io.FileNotFoundException { return null; }
    public static int parseMode(String mode) { return 0; }
    public long getStatSize() { return 0; }
    public java.io.FileDescriptor getFileDescriptor() { return null; }
    public void close() throws java.io.IOException { }
}''')
cls('android.os.PowerManager', '''
public final class PowerManager {
    public static final int PARTIAL_WAKE_LOCK = 1;
    public final class WakeLock { public void acquire(long timeout) { } public void release() { } public boolean isHeld() { return false; } }
    public WakeLock newWakeLock(int levelAndFlags, String tag) { return null; }
    public boolean isIgnoringBatteryOptimizations(String packageName) { return false; }
}''')
cls('android.provider.MediaStore', '''
public final class MediaStore {
    public static final class Images { public static final class Media { public static final android.net.Uri EXTERNAL_CONTENT_URI = null; }
        public static final class Thumbnails { public static final int MINI_KIND = 1;
            public static android.graphics.Bitmap getThumbnail(android.content.ContentResolver cr, long id, int kind, android.graphics.BitmapFactory.Options o) { return null; } } }
    public static final class Video { public static final class Media { public static final android.net.Uri EXTERNAL_CONTENT_URI = null; }
        public static final class Thumbnails { public static final int MINI_KIND = 1;
            public static android.graphics.Bitmap getThumbnail(android.content.ContentResolver cr, long id, int kind, android.graphics.BitmapFactory.Options o) { return null; } } }
    public static final String EXTRA_OUTPUT = "output", ACTION_IMAGE_CAPTURE = "ic", ACTION_VIDEO_CAPTURE = "vc";
    public interface MediaColumns { String DISPLAY_NAME = "_display_name", MIME_TYPE = "mime_type", RELATIVE_PATH = "relative_path", IS_PENDING = "is_pending"; }
    public static final class Downloads { public static final android.net.Uri EXTERNAL_CONTENT_URI = null; }
}''')
cls('android.provider.OpenableColumns', 'public interface OpenableColumns { String DISPLAY_NAME = "_display_name"; String SIZE = "_size"; }')
cls('android.provider.Settings', '''
public final class Settings {
    public static final String ACTION_APP_NOTIFICATION_SETTINGS = "n", EXTRA_APP_PACKAGE = "p", ACTION_APPLICATION_DETAILS_SETTINGS = "d",
        ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS = "r", ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS = "s",
        ACTION_CHANNEL_NOTIFICATION_SETTINGS = "c", EXTRA_CHANNEL_ID = "i";   // API 26
}''')
cls('android.security.NetworkSecurityPolicy', '''
public class NetworkSecurityPolicy {
    public static NetworkSecurityPolicy getInstance() { return null; }
    public boolean isCleartextTrafficPermitted(String hostname) { return false; }
}''')
cls('android.security.keystore.KeyProperties', '''
public abstract class KeyProperties {
    public static final String KEY_ALGORITHM_AES = "AES", BLOCK_MODE_GCM = "GCM", ENCRYPTION_PADDING_NONE = "NoPadding";
    public static final int PURPOSE_ENCRYPT = 1, PURPOSE_DECRYPT = 2;
}''')
cls('android.security.keystore.KeyGenParameterSpec', '''
public final class KeyGenParameterSpec implements java.security.spec.AlgorithmParameterSpec {
    public static final class Builder {
        public Builder(String keystoreAlias, int purposes) { }
        public Builder setBlockModes(String... blockModes) { return this; }
        public Builder setEncryptionPaddings(String... paddings) { return this; }
        public Builder setKeySize(int keySize) { return this; }
        public KeyGenParameterSpec build() { return null; }
    }
}''')
# view / widget / window
cls('android.view.Gravity', 'public class Gravity { public static final int CENTER = 17; }')
cls('android.view.Surface', '''
public class Surface { public Surface(android.graphics.SurfaceTexture surfaceTexture) { } public void release() { } }''')
cls('android.view.View', '''
public class View {
    public static final int SYSTEM_UI_FLAG_LIGHT_STATUS_BAR = 0x2000, SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR = 0x10;
    public interface OnApplyWindowInsetsListener { WindowInsets onApplyWindowInsets(View v, WindowInsets insets); }
    public interface OnClickListener { void onClick(View v); }
    public View(android.content.Context context) { }
    public void setPadding(int left, int top, int right, int bottom) { }
    public void setOnApplyWindowInsetsListener(OnApplyWindowInsetsListener listener) { }
    public void setFitsSystemWindows(boolean fitSystemWindows) { }
    public void setBackgroundColor(int color) { }
    public void setOnClickListener(OnClickListener l) { }
    @Deprecated public int getSystemUiVisibility() { return 0; }
    @Deprecated public void setSystemUiVisibility(int visibility) { }
    public static final int SYSTEM_UI_FLAG_FULLSCREEN = 4;
    public static final int SYSTEM_UI_FLAG_HIDE_NAVIGATION = 2;
    public static final int SYSTEM_UI_FLAG_IMMERSIVE_STICKY = 4096;
    public static final int SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN = 1024;
    public static final int SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION = 512;
    public static final int SYSTEM_UI_FLAG_LAYOUT_STABLE = 256;
}''')
cls('android.view.ViewGroup', '''
public abstract class ViewGroup extends View {
    public ViewGroup(android.content.Context context) { super(context); }
    public static class LayoutParams { public static final int MATCH_PARENT = -1; public LayoutParams(int width, int height) { } }
    public void addView(View child) { }
    public void addView(View child, LayoutParams params) { }
    public void addView(View child, int index, LayoutParams params) { }
    public void removeView(View view) { }
}''')
cls('android.view.Window', '''
public abstract class Window {
    public void setDecorFitsSystemWindows(boolean decorFitsSystemWindows) { }
    public WindowInsetsController getInsetsController() { return null; }
    @Deprecated public abstract void setStatusBarColor(int color);
    @Deprecated public abstract void setNavigationBarColor(int color);
    public abstract View getDecorView();
}''')
cls('android.view.WindowInsets', '''
public final class WindowInsets {
    public static final WindowInsets CONSUMED = null;
    public static final class Type { public static int systemBars() { return 0; } public static int displayCutout() { return 0; } public static int ime() { return 0; } }
    public android.graphics.Insets getInsets(int typeMask) { return null; }
}''')
cls('android.view.WindowInsetsController', '''
public interface WindowInsetsController {
    int APPEARANCE_LIGHT_STATUS_BARS = 8, APPEARANCE_LIGHT_NAVIGATION_BARS = 16;
    void setSystemBarsAppearance(int appearance, int mask);
}''')
cls('android.widget.FrameLayout', '''
public class FrameLayout extends android.view.ViewGroup {
    public FrameLayout(android.content.Context context) { super(context); }
    public static class LayoutParams extends android.view.ViewGroup.LayoutParams { public LayoutParams(int width, int height) { super(width, height); } }
}''')
cls('android.widget.LinearLayout', '''
public class LinearLayout extends android.view.ViewGroup {
    public static final int VERTICAL = 1;
    public LinearLayout(android.content.Context context) { super(context); }
    public void setOrientation(int orientation) { }
    public void setGravity(int gravity) { }
}''')
cls('android.widget.TextView', '''
public class TextView extends android.view.View {
    public TextView(android.content.Context context) { super(context); }
    public void setTextSize(float size) { }
    public void setGravity(int gravity) { }
    public final void setText(CharSequence text) { }
}''')
cls('android.widget.Button', 'public class Button extends TextView { public Button(android.content.Context context) { super(context); } }')
cls('android.widget.Toast', '''
public class Toast {
    public static final int LENGTH_SHORT = 0;
    public static Toast makeText(android.content.Context context, CharSequence text, int duration) { return null; }
    public void show() { }
}''')
cls('android.window.OnBackInvokedCallback', 'public interface OnBackInvokedCallback { void onBackInvoked(); }')
cls('android.window.OnBackInvokedDispatcher', '''
public interface OnBackInvokedDispatcher {
    int PRIORITY_DEFAULT = 0;
    void registerOnBackInvokedCallback(int priority, OnBackInvokedCallback callback);
}''')
# webkit
cls('android.webkit.ConsoleMessage', '''
public class ConsoleMessage { public String message() { return ""; } public String sourceId() { return ""; } public int lineNumber() { return 0; } }''')
cls('android.webkit.CookieManager', '''
public abstract class CookieManager { public static CookieManager getInstance() { return null; } public abstract void setAcceptThirdPartyCookies(WebView webview, boolean accept); }''')
cls('android.webkit.GeolocationPermissions', '''
public class GeolocationPermissions { public interface Callback { void invoke(String origin, boolean allow, boolean retain); } }''')
cls('android.webkit.MimeTypeMap', '''
public class MimeTypeMap { public static MimeTypeMap getSingleton() { return null; } public String getMimeTypeFromExtension(String extension) { return null; } }''')
cls('android.webkit.PermissionRequest', 'public abstract class PermissionRequest { public abstract void deny(); public abstract android.net.Uri getOrigin(); public abstract String[] getResources(); public abstract void grant(String[] resources); }')
cls('android.os.CancellationSignal', 'public final class CancellationSignal { }')
cls('android.webkit.RenderProcessGoneDetail', 'public abstract class RenderProcessGoneDetail { }')
cls('android.webkit.SslErrorHandler', 'public class SslErrorHandler { public void cancel() { } }')
cls('android.webkit.URLUtil', '''
public final class URLUtil { public static String guessFileName(String url, String contentDisposition, String mimeType) { return ""; } }''')
cls('android.webkit.ValueCallback', 'public interface ValueCallback<T> { void onReceiveValue(T value); }')
cls('android.webkit.DownloadListener', '''
public interface DownloadListener { void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength); }''')
cls('android.webkit.WebResourceRequest', '''
public interface WebResourceRequest {
    android.net.Uri getUrl();
    boolean isForMainFrame();
    String getMethod();
    java.util.Map<String, String> getRequestHeaders();
}''')
cls('android.webkit.WebResourceResponse', '''
public class WebResourceResponse {
    public WebResourceResponse(String mimeType, String encoding, int statusCode, String reasonPhrase, java.util.Map<String, String> responseHeaders, java.io.InputStream data) { }
    public java.util.Map<String, String> getResponseHeaders() { return null; }
}''')
cls('android.webkit.WebSettings', '''
public abstract class WebSettings {
    public static final int MIXED_CONTENT_NEVER_ALLOW = 1;
    public abstract void setJavaScriptEnabled(boolean flag);
    public abstract void setDomStorageEnabled(boolean flag);
    public abstract void setAllowFileAccess(boolean allow);
    public abstract void setAllowContentAccess(boolean allow);
    @Deprecated public abstract void setAllowFileAccessFromFileURLs(boolean flag);
    @Deprecated public abstract void setAllowUniversalAccessFromFileURLs(boolean flag);
    public abstract void setMixedContentMode(int mode);
    public abstract void setSupportMultipleWindows(boolean support);
    public abstract void setJavaScriptCanOpenWindowsAutomatically(boolean flag);
    public abstract void setMediaPlaybackRequiresUserGesture(boolean require);
    public abstract void setGeolocationEnabled(boolean flag);
    public abstract void setSupportZoom(boolean support);
    public abstract void setBuiltInZoomControls(boolean enabled);
    public abstract void setDisplayZoomControls(boolean enabled);
    public abstract void setUseWideViewPort(boolean use);
    public abstract void setLoadWithOverviewMode(boolean overview);
    public abstract void setTextZoom(int textZoom);
    public abstract String getUserAgentString();
    public abstract void setUserAgentString(String ua);
}''')
cls('android.webkit.WebView', '''
public class WebView extends android.widget.FrameLayout {
    public class WebViewTransport { public synchronized void setWebView(WebView webview) { } }
    public WebView(android.content.Context context) { super(context); }
    public static void setWebContentsDebuggingEnabled(boolean enabled) { }
    public WebSettings getSettings() { return null; }
    public void setWebViewClient(WebViewClient client) { }
    public void setWebChromeClient(WebChromeClient client) { }
    public void setDownloadListener(DownloadListener listener) { }
    public void loadUrl(String url) { }
    public void evaluateJavascript(String script, ValueCallback<String> resultCallback) { }
    public boolean canGoBack() { return false; }
    public void goBack() { }
    public String getUrl() { return null; }
    public void stopLoading() { }
    public void destroy() { }
    public void onResume() { }
    public void onPause() { }
    public void resumeTimers() { }
}''')
cls('android.webkit.WebViewClient', '''
public class WebViewClient {
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) { return null; }
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return false; }
    public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) { }
    public void onPageFinished(WebView view, String url) { }
    public void onReceivedSslError(WebView view, SslErrorHandler handler, android.net.http.SslError error) { }
    public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) { return false; }
}''')
cls('android.webkit.WebChromeClient', '''
public class WebChromeClient {
    public interface CustomViewCallback { void onCustomViewHidden(); }
    public abstract static class FileChooserParams {
        public static final int MODE_OPEN = 0, MODE_OPEN_MULTIPLE = 1;
        public abstract int getMode();
        public abstract String[] getAcceptTypes();
    }
    public boolean onShowFileChooser(WebView webView, ValueCallback<android.net.Uri[]> filePathCallback, FileChooserParams fileChooserParams) { return false; }
    public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, android.os.Message resultMsg) { return false; }
    public void onCloseWindow(WebView window) { }
    public void onPermissionRequest(PermissionRequest request) { }
    public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) { }
    public void onShowCustomView(android.view.View view, CustomViewCallback callback) { }
    public void onHideCustomView() { }
    public android.graphics.Bitmap getDefaultVideoPoster() { return null; }
    public boolean onConsoleMessage(ConsoleMessage consoleMessage) { return false; }
}''')
# ── androidx.webkit ──────────────────────────────────────────────────────
cls('androidx.webkit.JavaScriptReplyProxy', 'public abstract class JavaScriptReplyProxy { public abstract void postMessage(String message); }')
cls('androidx.webkit.WebMessageCompat', 'public class WebMessageCompat { public String getData() { return null; } }')
cls('androidx.webkit.WebSettingsCompat', '''
public class WebSettingsCompat {
    @Deprecated public static final int FORCE_DARK_OFF = 0;
    public static void setAlgorithmicDarkeningAllowed(android.webkit.WebSettings settings, boolean allow) { }
    @Deprecated public static void setForceDark(android.webkit.WebSettings settings, int forceDarkMode) { }
}''')
cls('androidx.webkit.WebViewCompat', '''
public class WebViewCompat {
    public interface WebMessageListener {
        void onPostMessage(android.webkit.WebView view, WebMessageCompat message, android.net.Uri sourceOrigin, boolean isMainFrame, JavaScriptReplyProxy replyProxy);
    }
    public static void addWebMessageListener(android.webkit.WebView webView, String jsObjectName, java.util.Set<String> allowedOriginRules, WebMessageListener listener) { }
    public static android.content.pm.PackageInfo getCurrentWebViewPackage(android.content.Context context) { return null; }
}''')
cls('androidx.webkit.WebViewFeature', '''
public class WebViewFeature {
    public static final String WEB_MESSAGE_LISTENER = "WEB_MESSAGE_LISTENER", ALGORITHMIC_DARKENING = "ALGORITHMIC_DARKENING", FORCE_DARK = "FORCE_DARK";
    public static boolean isFeatureSupported(String feature) { return false; }
}''')
# ── Google ───────────────────────────────────────────────────────────────
cls('com.google.android.gms.common.ConnectionResult', 'public class ConnectionResult { public static final int SUCCESS = 0; }')
# play-services-basement (приходит с firebase-messaging); GoogleApiAvailability из
# play-services-base при компиляции недоступен — заглушки для него нет намеренно
cls('com.google.android.gms.common.GoogleApiAvailabilityLight', '''
public class GoogleApiAvailabilityLight {
    public static GoogleApiAvailabilityLight getInstance() { return null; }
    public int isGooglePlayServicesAvailable(android.content.Context context) { return 1; }
}''')
cls('com.google.android.gms.tasks.Task', 'public abstract class Task<TResult> { }')
cls('com.google.android.gms.tasks.Tasks', '''
public final class Tasks {
    public static <TResult> TResult await(Task<TResult> task, long timeout, java.util.concurrent.TimeUnit unit)
        throws java.util.concurrent.ExecutionException, InterruptedException, java.util.concurrent.TimeoutException { return null; }
}''')
cls('com.google.firebase.FirebaseApp', '''
public class FirebaseApp {
    public static java.util.List<FirebaseApp> getApps(android.content.Context context) { return null; }
    public static FirebaseApp initializeApp(android.content.Context context, FirebaseOptions options) { return null; }
}''')
cls('com.google.firebase.FirebaseOptions', '''
public final class FirebaseOptions {
    public static final class Builder {
        public Builder setProjectId(String projectId) { return this; }
        public Builder setApplicationId(String applicationId) { return this; }
        public Builder setApiKey(String apiKey) { return this; }
        public Builder setGcmSenderId(String gcmSenderId) { return this; }
        public FirebaseOptions build() { return null; }
    }
}''')
cls('com.google.firebase.messaging.FirebaseMessaging', '''
public class FirebaseMessaging {
    public static synchronized FirebaseMessaging getInstance() { return null; }
    public com.google.android.gms.tasks.Task<String> getToken() { return null; }
    public com.google.android.gms.tasks.Task<Void> deleteToken() { return null; }
}''')
cls('com.google.firebase.messaging.RemoteMessage', 'public final class RemoteMessage { public java.util.Map<String, String> getData() { return null; } }')
cls('com.google.firebase.messaging.FirebaseMessagingService', '''
public class FirebaseMessagingService extends android.app.Service {
    public final android.os.IBinder onBind(android.content.Intent intent) { return null; }
    public void onMessageReceived(RemoteMessage message) { }
    public void onNewToken(String token) { }
}''')

def write(out, flavor):
    for fq, (header, body) in S.items():
        pkg, name = fq.rsplit('.', 1)
        path = os.path.join(out, *pkg.split('.'), name + '.java')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('package %s;\n\n%s\n' % (pkg, body))
    # сгенерированные сборкой классы
    bc = os.path.join(out, 'ru', 'kovcheg', 'kovapp')
    os.makedirs(bc, exist_ok=True)
    with open(os.path.join(bc, 'BuildConfig.java'), 'w', encoding='utf-8') as f:
        f.write('''package ru.kovcheg.kovapp;
public final class BuildConfig {
    public static final boolean DEBUG = Boolean.parseBoolean("true");
    public static final String APPLICATION_ID = "ru.kovcheg.kovapp", BUILD_TYPE = "debug", FLAVOR = "%s";
    public static final int VERSION_CODE = 1;
    public static final String VERSION_NAME = "1.0.0";
    public static final String SERVER_ORIGIN = "https://kovcheg.example.ru", APP_PATH = "/app/", API_ORIGIN = "", CSP_OVERRIDE = "", DEV_ASSETS_URL = "";
    public static final boolean WEB_PATCHES = true;
    public static final String FCM_PROJECT_ID = "", FCM_APP_ID = "", FCM_API_KEY = "", FCM_SENDER_ID = "";
}
''' % flavor)
    with open(os.path.join(bc, 'R.java'), 'w', encoding='utf-8') as f:
        f.write('''package ru.kovcheg.kovapp;
public final class R {
    public static final class drawable { public static final int ic_stat_kovapp = 0x7f010001; }
    public static final class mipmap { public static final int ic_launcher = 0x7f020001; }
    public static final class string { public static final int app_name = 0x7f030001; }
}
''')

if __name__ == '__main__':
    flavor = sys.argv[2] if len(sys.argv) > 2 else 'universal'
    write(OUT, flavor)
    print('stubs:', len(S), '->', OUT)
