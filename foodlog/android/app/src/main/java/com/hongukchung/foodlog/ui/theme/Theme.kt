package com.hongukchung.foodlog.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val LightColors = lightColorScheme(
    primary = Color(0xFFB43C1B),
    secondary = Color(0xFF77574C),
    tertiary = Color(0xFF6A5F31),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFFFB4A0),
    secondary = Color(0xFFE7BDB0),
    tertiary = Color(0xFFD6C78F),
)

/** 신뢰도 낮은 AI 추정 항목 표시용 (스펙 6절: confidence < 0.5 주황색) */
val WarningOrange = Color(0xFFE65100)

@Composable
fun FoodLogTheme(content: @Composable () -> Unit) {
    val darkTheme = isSystemInDarkTheme()
    val colorScheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColors
        else -> LightColors
    }
    MaterialTheme(colorScheme = colorScheme, content = content)
}
