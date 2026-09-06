package com.hongukchung.foodlog.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.hongukchung.foodlog.BuildConfig
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "settings")

data class Settings(
    val serverBaseUrl: String,
    val appToken: String,
    val dailyGoalKcal: Int,
    val imageMaxEdgePx: Int,
    val lastBackupAt: Long,
    val keepOriginalMonths: Int,   // 0 = 원본 무기한 보관
)

class SettingsRepository(private val context: Context) {
    private object Keys {
        val serverBaseUrl = stringPreferencesKey("serverBaseUrl")
        val appToken = stringPreferencesKey("appToken")
        val dailyGoalKcal = intPreferencesKey("dailyGoalKcal")
        val imageMaxEdgePx = intPreferencesKey("imageMaxEdgePx")
        val lastBackupAt = longPreferencesKey("lastBackupAt")
        val keepOriginalMonths = intPreferencesKey("keepOriginalMonths")
    }

    val settings: Flow<Settings> = context.dataStore.data.map { p ->
        Settings(
            serverBaseUrl = p[Keys.serverBaseUrl] ?: BuildConfig.DEFAULT_SERVER_URL,
            appToken = p[Keys.appToken] ?: BuildConfig.DEFAULT_APP_TOKEN,
            dailyGoalKcal = p[Keys.dailyGoalKcal] ?: 2000,
            imageMaxEdgePx = p[Keys.imageMaxEdgePx] ?: 1280,
            lastBackupAt = p[Keys.lastBackupAt] ?: 0L,
            keepOriginalMonths = p[Keys.keepOriginalMonths] ?: 0,
        )
    }

    suspend fun current(): Settings = settings.first()

    suspend fun setServerBaseUrl(v: String) = context.dataStore.edit { it[Keys.serverBaseUrl] = v.trim().trimEnd('/') }
    suspend fun setAppToken(v: String) = context.dataStore.edit { it[Keys.appToken] = v.trim() }
    suspend fun setDailyGoalKcal(v: Int) = context.dataStore.edit { it[Keys.dailyGoalKcal] = v }
    suspend fun setImageMaxEdgePx(v: Int) = context.dataStore.edit { it[Keys.imageMaxEdgePx] = v }
    suspend fun setLastBackupAt(v: Long) = context.dataStore.edit { it[Keys.lastBackupAt] = v }
    suspend fun setKeepOriginalMonths(v: Int) = context.dataStore.edit { it[Keys.keepOriginalMonths] = v }
}
