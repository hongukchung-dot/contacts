package com.hongukchung.foodlog.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.util.Base64
import androidx.exifinterface.media.ExifInterface
import com.hongukchung.foodlog.data.db.Photo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.UUID

/**
 * 사진 저장 정책 (스펙 8절)
 *  - 원본: filesDir/photos/{id}.jpg (앱 전용, 갤러리 미노출)
 *  - 썸네일: filesDir/thumbs/{id}.jpg, 320px (영구 저장)
 *  - 갤러리에서 가져온 사진은 앱 저장소로 복사
 */
class PhotoStore(private val context: Context) {

    val photosDir: File get() = File(context.filesDir, "photos").apply { mkdirs() }
    val thumbsDir: File get() = File(context.filesDir, "thumbs").apply { mkdirs() }

    fun photoFile(id: String): File = File(photosDir, "$id.jpg")
    fun thumbFile(id: String): File = File(thumbsDir, "$id.jpg")

    /** 카메라 촬영 결과용 새 파일 경로. 저장 후 [registerCaptured] 호출. */
    fun newCaptureTarget(): Pair<String, File> {
        val id = UUID.randomUUID().toString()
        return id to photoFile(id)
    }

    /** 촬영된 파일을 Photo 엔티티로 만들고 썸네일 생성 */
    suspend fun registerCaptured(id: String, file: File): Photo = withContext(Dispatchers.IO) {
        val takenAt = exifTakenAt(file) ?: file.lastModified().takeIf { it > 0 } ?: System.currentTimeMillis()
        makeThumbnail(file, thumbFile(id))
        Photo(
            id = id,
            filePath = "photos/$id.jpg",
            takenAt = takenAt,
            mealId = null,
            createdAt = System.currentTimeMillis(),
        )
    }

    /** 갤러리(사진 선택기) URI를 앱 저장소로 복사 */
    suspend fun importFromUri(uri: Uri): Photo? = withContext(Dispatchers.IO) {
        val id = UUID.randomUUID().toString()
        val dest = photoFile(id)
        try {
            context.contentResolver.openInputStream(uri)?.use { input ->
                dest.outputStream().use { output -> input.copyTo(output) }
            } ?: return@withContext null
            val takenAt = exifTakenAt(dest) ?: System.currentTimeMillis()
            makeThumbnail(dest, thumbFile(id))
            Photo(
                id = id,
                filePath = "photos/$id.jpg",
                takenAt = takenAt,
                mealId = null,
                createdAt = System.currentTimeMillis(),
            )
        } catch (e: Exception) {
            dest.delete()
            null
        }
    }

    fun resolve(photo: Photo): File {
        val original = File(context.filesDir, photo.filePath)
        return if (original.exists()) original else thumbFile(photo.id)
    }

    fun resolveThumb(photo: Photo): File {
        val thumb = thumbFile(photo.id)
        return if (thumb.exists()) thumb else File(context.filesDir, photo.filePath)
    }

    fun deleteFiles(photo: Photo) {
        File(context.filesDir, photo.filePath).delete()
        thumbFile(photo.id).delete()
    }

    /** 전송용 리사이즈: 긴 변 maxEdge, JPEG 품질 80 → base64 (스펙 6절) */
    suspend fun toUploadBase64(photo: Photo, maxEdge: Int): String? = withContext(Dispatchers.IO) {
        val file = resolve(photo)
        if (!file.exists()) return@withContext null
        val bitmap = decodeScaled(file, maxEdge) ?: return@withContext null
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 80, out)
        bitmap.recycle()
        Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    /** 저장 공간 사용량 (bytes): 원본 + 썸네일 */
    fun storageUsageBytes(): Long =
        (photosDir.listFiles()?.sumOf { it.length() } ?: 0L) +
            (thumbsDir.listFiles()?.sumOf { it.length() } ?: 0L)

    /** N개월 지난 (끼니에 속한) 원본 삭제, 썸네일만 유지. 삭제한 파일 수 반환 */
    fun deleteOriginal(photo: Photo): Boolean {
        val original = File(context.filesDir, photo.filePath)
        // 썸네일이 있어야만 원본을 지운다
        return thumbFile(photo.id).exists() && original.exists() && original.delete()
    }

    // ------------------------------------------------------------------
    private fun exifTakenAt(file: File): Long? = try {
        val exif = ExifInterface(file)
        val dt = exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL)
            ?: exif.getAttribute(ExifInterface.TAG_DATETIME)
        dt?.let {
            SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US).parse(it)?.time
        }
    } catch (e: Exception) {
        null
    }

    private fun exifRotationDegrees(file: File): Int = try {
        when (ExifInterface(file).getAttributeInt(
            ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL,
        )) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90
            ExifInterface.ORIENTATION_ROTATE_180 -> 180
            ExifInterface.ORIENTATION_ROTATE_270 -> 270
            else -> 0
        }
    } catch (e: Exception) {
        0
    }

    private fun decodeScaled(file: File, maxEdge: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= maxEdge) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        var bitmap = BitmapFactory.decodeFile(file.absolutePath, opts) ?: return null

        val longEdge = maxOf(bitmap.width, bitmap.height)
        if (longEdge > maxEdge) {
            val scale = maxEdge.toFloat() / longEdge
            val scaled = Bitmap.createScaledBitmap(
                bitmap,
                (bitmap.width * scale).toInt().coerceAtLeast(1),
                (bitmap.height * scale).toInt().coerceAtLeast(1),
                true,
            )
            if (scaled !== bitmap) bitmap.recycle()
            bitmap = scaled
        }

        val rotation = exifRotationDegrees(file)
        if (rotation != 0) {
            val matrix = Matrix().apply { postRotate(rotation.toFloat()) }
            val rotated = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
            if (rotated !== bitmap) bitmap.recycle()
            bitmap = rotated
        }
        return bitmap
    }

    private fun makeThumbnail(src: File, dest: File) {
        val bitmap = decodeScaled(src, THUMB_EDGE) ?: return
        dest.outputStream().use { out ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)
        }
        bitmap.recycle()
    }

    companion object {
        const val THUMB_EDGE = 320
    }
}
