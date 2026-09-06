import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

android {
    namespace = "com.hongukchung.foodlog"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.hongukchung.foodlog"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    // admin / friend 플레이버 — 서버 URL·앱 토큰만 다르다. Anthropic 키는 앱에 없음.
    // 실제 값은 local.properties 또는 CI 환경변수로 주입하고, 없으면 빈 값(설정 화면에서 입력).
    flavorDimensions += "dist"
    productFlavors {
        create("admin") {
            dimension = "dist"
            buildConfigField("String", "DEFAULT_SERVER_URL", "\"${prop("FOODLOG_SERVER_URL")}\"")
            buildConfigField("String", "DEFAULT_APP_TOKEN", "\"${prop("FOODLOG_ADMIN_TOKEN")}\"")
            buildConfigField("boolean", "SERVER_EDITABLE", "true")
        }
        create("friend") {
            dimension = "dist"
            applicationIdSuffix = ".friend"
            buildConfigField("String", "DEFAULT_SERVER_URL", "\"${prop("FOODLOG_SERVER_URL")}\"")
            buildConfigField("String", "DEFAULT_APP_TOKEN", "\"${prop("FOODLOG_FRIEND_TOKEN")}\"")
            buildConfigField("boolean", "SERVER_EDITABLE", "false")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

fun prop(name: String): String {
    val fromEnv = System.getenv(name)
    if (fromEnv != null) return fromEnv
    val f = rootProject.file("local.properties")
    if (!f.exists()) return ""
    val p = Properties()
    f.inputStream().use { stream -> p.load(stream) }
    return p.getProperty(name) ?: ""
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.androidx.navigation.compose)

    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)

    implementation(libs.androidx.datastore.preferences)

    implementation(libs.androidx.camera.core)
    implementation(libs.androidx.camera.camera2)
    implementation(libs.androidx.camera.lifecycle)
    implementation(libs.androidx.camera.view)
    implementation(libs.androidx.exifinterface)

    implementation(libs.coil.compose)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.guava)

    // ML Kit 바코드 — 번들형(오프라인 안정, APK 약 +3MB). 스펙 10절 결정 사항.
    implementation(libs.mlkit.barcode.scanning)

    implementation(libs.vico.compose.m3)

    debugImplementation(libs.androidx.compose.ui.tooling)
}
