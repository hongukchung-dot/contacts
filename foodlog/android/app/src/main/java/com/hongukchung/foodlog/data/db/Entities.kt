package com.hongukchung.foodlog.data.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.TypeConverter
import kotlinx.serialization.Serializable

enum class MealType { BREAKFAST, LUNCH, DINNER, SNACK }

enum class MealStatus { DRAFT, ANALYZING, REVIEWED }

enum class ItemSource { PHOTO_AI, BARCODE, LABEL_AI, MANUAL }

@Serializable
@Entity(indices = [Index("mealId"), Index("takenAt")])
data class Photo(
    @PrimaryKey val id: String,          // UUID
    val filePath: String,                // 앱 전용 저장소 내 경로 (filesDir 기준 상대경로)
    val takenAt: Long,                   // epoch millis (EXIF 우선, 없으면 파일 생성 시각)
    val mealId: String? = null,          // null이면 "미분석 사진함"에 표시
    val createdAt: Long,
)

@Serializable
@Entity(indices = [Index("eatenAt"), Index("status")])
data class Meal(
    @PrimaryKey val id: String,
    val eatenAt: Long,                   // 끼니 시각 (사진 중 가장 이른 takenAt 기본값)
    val mealType: MealType,              // 시간대로 자동 제안
    val peopleCount: Int = 1,            // 나눠 먹은 인원 (공유 항목에만 적용)
    val status: MealStatus,
    val note: String? = null,
    val createdAt: Long,
    val updatedAt: Long,
)

@Serializable
@Entity(indices = [Index("mealId")])
data class FoodItem(
    @PrimaryKey val id: String,
    val mealId: String,
    val name: String,                    // "김치찌개", "삼겹살 3인분"
    val source: ItemSource,
    val portionDesc: String? = null,     // "1인분(약 300g)", "1봉지(90g)"
    val quantity: Double = 1.0,          // 사용자가 조정하는 배수
    val kcalPerUnit: Double,             // quantity=1 기준 열량
    val carbsG: Double? = null,
    val proteinG: Double? = null,
    val fatG: Double? = null,
    val isShared: Boolean = false,       // true면 meal.peopleCount로 나눔
    val confidence: Float? = null,       // AI 추정 신뢰도 0~1
    val barcode: String? = null,
    val sortOrder: Int = 0,
)

@Serializable
@Entity
data class Product(                      // 바코드 조회 캐시
    @PrimaryKey val barcode: String,
    val name: String,
    val brand: String? = null,
    val servingDesc: String? = null,
    val kcalPerServing: Double,
    val carbsG: Double? = null,
    val proteinG: Double? = null,
    val fatG: Double? = null,
    val source: String,                  // "MFDS", "OFF", "LABEL_AI", "MANUAL"
    val fetchedAt: Long,
)

class Converters {
    @TypeConverter fun mealTypeToString(v: MealType): String = v.name
    @TypeConverter fun stringToMealType(v: String): MealType = MealType.valueOf(v)
    @TypeConverter fun mealStatusToString(v: MealStatus): String = v.name
    @TypeConverter fun stringToMealStatus(v: String): MealStatus = MealStatus.valueOf(v)
    @TypeConverter fun itemSourceToString(v: ItemSource): String = v.name
    @TypeConverter fun stringToItemSource(v: String): ItemSource = ItemSource.valueOf(v)
}

/** 열량 계산 규칙 (스펙 3절) */
fun FoodItem.totalKcal(): Double = kcalPerUnit * quantity

fun FoodItem.myKcal(peopleCount: Int): Double =
    if (isShared) totalKcal() / peopleCount.coerceAtLeast(1) else totalKcal()
