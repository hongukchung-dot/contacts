package com.hongukchung.foodlog.data

import android.content.Context
import android.net.Uri
import com.hongukchung.foodlog.data.db.AppDatabase
import com.hongukchung.foodlog.data.db.FoodItem
import com.hongukchung.foodlog.data.db.Meal
import com.hongukchung.foodlog.data.db.Photo
import com.hongukchung.foodlog.data.db.Product
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

/**
 * 백업/복원 — 방식 A (스펙 7절)
 * foodlog-backup-YYYYMMDD.zip:
 *   manifest.json  앱 버전, 스키마 버전, 생성 시각
 *   db.json        Meal, FoodItem, Product, Photo 메타데이터 전체
 *   photos/        원본 사진 (파일명 = Photo.id + ".jpg")
 */
class BackupManager(
    private val context: Context,
    private val db: AppDatabase,
    private val photoStore: PhotoStore,
) {
    @Serializable
    data class Manifest(
        val app: String = "foodlog",
        val appVersion: String,
        val schemaVersion: Int,
        val createdAt: Long,
    )

    @Serializable
    data class DbDump(
        val meals: List<Meal>,
        val foodItems: List<FoodItem>,
        val products: List<Product>,
        val photos: List<Photo>,
    )

    enum class RestoreMode { MERGE, REPLACE }

    data class RestoreResult(
        val meals: Int, val items: Int, val products: Int, val photos: Int,
    )

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    suspend fun export(dest: Uri, appVersion: String): Boolean = withContext(Dispatchers.IO) {
        try {
            val dump = DbDump(
                meals = db.mealDao().all(),
                foodItems = db.foodItemDao().all(),
                products = db.productDao().all(),
                photos = db.photoDao().all(),
            )
            val manifest = Manifest(
                appVersion = appVersion,
                schemaVersion = AppDatabase.SCHEMA_VERSION,
                createdAt = System.currentTimeMillis(),
            )
            context.contentResolver.openOutputStream(dest)?.use { raw ->
                ZipOutputStream(raw.buffered()).use { zip ->
                    zip.putNextEntry(ZipEntry("manifest.json"))
                    zip.write(json.encodeToString(manifest).toByteArray())
                    zip.closeEntry()

                    zip.putNextEntry(ZipEntry("db.json"))
                    zip.write(json.encodeToString(dump).toByteArray())
                    zip.closeEntry()

                    for (photo in dump.photos) {
                        val file = File(context.filesDir, photo.filePath)
                        val src = if (file.exists()) file else photoStore.thumbFile(photo.id)
                        if (!src.exists()) continue
                        zip.putNextEntry(ZipEntry("photos/${photo.id}.jpg"))
                        src.inputStream().use { it.copyTo(zip) }
                        zip.closeEntry()
                    }
                }
            } ?: return@withContext false
            true
        } catch (e: Exception) {
            false
        }
    }

    suspend fun restore(src: Uri, mode: RestoreMode): RestoreResult? = withContext(Dispatchers.IO) {
        // 1차 패스: zip에서 db.json과 사진을 임시 폴더로 풀기
        val tempDir = File(context.cacheDir, "restore-${System.currentTimeMillis()}").apply { mkdirs() }
        try {
            var dump: DbDump? = null
            context.contentResolver.openInputStream(src)?.use { raw ->
                ZipInputStream(raw.buffered()).use { zip ->
                    var entry: ZipEntry? = zip.nextEntry
                    while (entry != null) {
                        val name = entry.name
                        when {
                            name == "db.json" ->
                                dump = json.decodeFromString<DbDump>(zip.readBytes().decodeToString())
                            name.startsWith("photos/") && !entry.isDirectory -> {
                                val fileName = File(name).name
                                // zip 경로 조작 방어: 파일명만 사용
                                if (fileName.isNotBlank()) {
                                    File(tempDir, fileName).outputStream().use { zip.copyTo(it) }
                                }
                            }
                        }
                        zip.closeEntry()
                        entry = zip.nextEntry
                    }
                }
            } ?: return@withContext null
            val data = dump ?: return@withContext null

            if (mode == RestoreMode.REPLACE) {
                // 기존 데이터·파일 전부 제거
                db.photoDao().all().forEach { photoStore.deleteFiles(it) }
                db.clearAllTables()
            }

            // 사진 파일 복사 (같은 id의 파일이 이미 있으면 건너뜀)
            var photoFiles = 0
            for (photo in data.photos) {
                val restored = File(tempDir, "${photo.id}.jpg")
                if (!restored.exists()) continue
                val dest = photoStore.photoFile(photo.id)
                if (mode == RestoreMode.REPLACE || !dest.exists()) {
                    restored.copyTo(dest, overwrite = true)
                    photoFiles++
                }
                if (!photoStore.thumbFile(photo.id).exists()) {
                    // 썸네일 재생성
                    photoStore.registerCaptured(photo.id, dest)
                }
            }

            // 메타데이터: MERGE는 같은 id 건너뜀(IGNORE), REPLACE는 빈 DB에 삽입
            db.mealDao().insertIgnore(data.meals)
            db.foodItemDao().insertIgnore(data.foodItems)
            db.productDao().insertIgnore(data.products)
            db.photoDao().insertIgnore(data.photos)

            RestoreResult(
                meals = data.meals.size,
                items = data.foodItems.size,
                products = data.products.size,
                photos = photoFiles,
            )
        } catch (e: Exception) {
            null
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
