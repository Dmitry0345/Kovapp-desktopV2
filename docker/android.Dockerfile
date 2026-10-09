# ===== Android в Docker: сборка APK и сервер Appium (эмулятор — Android Studio на ПК) =====
FROM eclipse-temurin:17-jdk

ENV ANDROID_SDK_ROOT=/opt/android-sdk \
    ANDROID_HOME=/opt/android-sdk \
    GRADLE_VERSION=8.14.3 \
    NODE_VERSION=22.12.0 \
    GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.jvmargs=-Xmx2g"

RUN apt-get update && apt-get install -y --no-install-recommends unzip curl rsync python3 socat xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Android SDK: инструменты, платформа, сборочные утилиты (нужны и Gradle, и Appium)
RUN mkdir -p $ANDROID_SDK_ROOT/cmdline-tools && cd /tmp \
    && curl -fsSLo tools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
    && unzip -q tools.zip -d $ANDROID_SDK_ROOT/cmdline-tools \
    && mv $ANDROID_SDK_ROOT/cmdline-tools/cmdline-tools $ANDROID_SDK_ROOT/cmdline-tools/latest \
    && rm tools.zip
ENV PATH=$PATH:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:/opt/gradle/bin:/opt/node/bin
RUN yes | sdkmanager --licenses > /dev/null \
    && sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0" > /dev/null

# Gradle — сборка APK
RUN curl -fsSLo /tmp/gradle.zip https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip \
    && unzip -q /tmp/gradle.zip -d /opt && mv /opt/gradle-${GRADLE_VERSION} /opt/gradle && rm /tmp/gradle.zip

# Node.js + Appium + драйвер UiAutomator2 (системная часть телефона и WebView через Chromedriver)
RUN curl -fsSLo /tmp/node.tar.xz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    && mkdir -p /opt/node && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 && rm /tmp/node.tar.xz \
    && npm install -g appium --no-fund --no-audit \
    && appium driver install uiautomator2

COPY apk-build.sh appium-start.sh link.py keyfiles.py fcm_args.py /opt/kovapp/
RUN sed -i 's/\r$//' /opt/kovapp/*.sh && chmod +x /opt/kovapp/*.sh
