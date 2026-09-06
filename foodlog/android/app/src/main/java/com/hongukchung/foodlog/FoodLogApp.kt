package com.hongukchung.foodlog

import android.app.Application
import com.hongukchung.foodlog.data.BackupManager
import com.hongukchung.foodlog.data.MealRepository
import com.hongukchung.foodlog.data.PhotoStore
import com.hongukchung.foodlog.data.SettingsRepository
import com.hongukchung.foodlog.data.db.AppDatabase
import com.hongukchung.foodlog.net.FoodLogApi

/** 수동 DI 컨테이너 — 앱 규모상 프레임워크 없이 Application 에 둔다 */
class FoodLogApp : Application() {
    val database: AppDatabase by lazy { AppDatabase.build(this) }
    val settings: SettingsRepository by lazy { SettingsRepository(this) }
    val photoStore: PhotoStore by lazy { PhotoStore(this) }
    val mealRepository: MealRepository by lazy { MealRepository(database, photoStore) }
    val api: FoodLogApi by lazy { FoodLogApi { settings.current() } }
    val backupManager: BackupManager by lazy { BackupManager(this, database, photoStore) }
}
