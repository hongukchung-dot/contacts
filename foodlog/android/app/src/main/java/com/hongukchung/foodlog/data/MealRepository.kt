package com.hongukchung.foodlog.data

import com.hongukchung.foodlog.data.db.AppDatabase
import com.hongukchung.foodlog.data.db.FoodItem
import com.hongukchung.foodlog.data.db.ItemSource
import com.hongukchung.foodlog.data.db.Meal
import com.hongukchung.foodlog.data.db.MealStatus
import com.hongukchung.foodlog.data.db.Photo
import com.hongukchung.foodlog.data.db.Product
import com.hongukchung.foodlog.util.suggestMealType
import java.util.UUID

class MealRepository(
    private val db: AppDatabase,
    private val photoStore: PhotoStore,
) {
    /** 미분석 사진을 시간 간격 기준으로 자동 그룹 제안 (기본 90분) */
    fun suggestGroups(photos: List<Photo>, gapMinutes: Long = 90): List<List<Photo>> {
        if (photos.isEmpty()) return emptyList()
        val sorted = photos.sortedBy { it.takenAt }
        val groups = mutableListOf<MutableList<Photo>>()
        var current = mutableListOf(sorted.first())
        for (p in sorted.drop(1)) {
            if (p.takenAt - current.last().takenAt <= gapMinutes * 60_000) {
                current.add(p)
            } else {
                groups.add(current)
                current = mutableListOf(p)
            }
        }
        groups.add(current)
        return groups
    }

    /** 선택한 사진들로 DRAFT 끼니 생성 → mealId 반환 */
    suspend fun createMealFromPhotos(photos: List<Photo>): String {
        val now = System.currentTimeMillis()
        val eatenAt = photos.minOfOrNull { it.takenAt } ?: now
        val meal = Meal(
            id = UUID.randomUUID().toString(),
            eatenAt = eatenAt,
            mealType = suggestMealType(eatenAt),
            peopleCount = 1,
            status = MealStatus.DRAFT,
            note = null,
            createdAt = now,
            updatedAt = now,
        )
        db.mealDao().insert(meal)
        if (photos.isNotEmpty()) {
            db.photoDao().assignToMeal(photos.map { it.id }, meal.id)
        }
        return meal.id
    }

    /** 빈 DRAFT 끼니 생성 (직접 입력·바코드 진입용) */
    suspend fun createEmptyMeal(): String = createMealFromPhotos(emptyList())

    /** 끼니 삭제 — 사진은 미분석함으로 되돌린다 */
    suspend fun deleteMeal(mealId: String) {
        db.photoDao().byMeal(mealId).forEach { db.photoDao().unassign(it.id) }
        db.foodItemDao().deleteByMeal(mealId)
        db.mealDao().deleteById(mealId)
    }

    /** 사진 완전 삭제 (파일 포함) */
    suspend fun deletePhoto(photo: Photo) {
        db.photoDao().delete(photo)
        photoStore.deleteFiles(photo)
    }

    /** 바코드 제품 → 항목 추가 (+ Product 캐시 저장) */
    suspend fun addItemFromProduct(mealId: String, product: Product, quantity: Double): FoodItem {
        db.productDao().insert(product)
        val item = FoodItem(
            id = UUID.randomUUID().toString(),
            mealId = mealId,
            name = product.name,
            source = if (product.source == "LABEL_AI") ItemSource.LABEL_AI else ItemSource.BARCODE,
            portionDesc = product.servingDesc,
            quantity = quantity,
            kcalPerUnit = product.kcalPerServing,
            carbsG = product.carbsG,
            proteinG = product.proteinG,
            fatG = product.fatG,
            isShared = false,
            confidence = null,
            barcode = product.barcode,
            sortOrder = db.foodItemDao().nextSortOrder(mealId),
        )
        db.foodItemDao().insert(item)
        touchMeal(mealId)
        return item
    }

    suspend fun touchMeal(mealId: String) {
        db.mealDao().byId(mealId)?.let {
            db.mealDao().update(it.copy(updatedAt = System.currentTimeMillis()))
        }
    }

    /** N개월 지난 원본 삭제 (썸네일 유지). 삭제 개수 반환 */
    suspend fun cleanupOriginalsOlderThan(months: Int): Int {
        if (months <= 0) return 0
        val cutoff = System.currentTimeMillis() - months * 30L * 24 * 60 * 60 * 1000
        var deleted = 0
        db.photoDao().olderThan(cutoff).forEach { photo ->
            if (photoStore.deleteOriginal(photo)) deleted++
        }
        return deleted
    }
}
