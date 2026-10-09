package ru.kovcheg.kovapp.files;

/** Строки уведомления об отправке: «Отправка 3 файлов», «45% · можно свернуть приложение» */
public final class TransferText {
    private TransferText() { }

    public static String title(int count) {
        if (count <= 1) return "Отправка файла";
        int m10 = count % 10, m100 = count % 100;
        return "Отправка " + count + (m10 == 1 && m100 != 11 ? " файла" : " файлов");
    }

    public static String text(int percent) {
        if (percent <= 0) return "Подготовка…";
        return Math.min(100, percent) + "% · можно свернуть приложение";
    }
}
