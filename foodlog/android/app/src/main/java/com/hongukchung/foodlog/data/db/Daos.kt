package com.hongukchung.foodlog.data.db

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Embedded
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Relation
import androidx.room.Transaction
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

data class MealWithItems(
    @Embedded val meal: Meal,
    @Relation(parentColumn = "id", entityColumn = "mealId")
    val items: List<FoodItem>,
    @Relation(parentColumn = "id", entityColumn = "mealId")
    val photos: List<Photo>,
) {
    fun myTotalKcal(): Double = items.sumOf { it.myKcal(meal.peopleCount) }
    fun totalKcal(): Double = items.sumOf { it.totalKcal() }
}

/** 날짜별 집계 (REVIEWED 끼니만, 기기 로컬 타임존) */
data class DayKcal(val day: String, val kcal: Double)
data class PeriodKcal(val period: String, val kcal: Double, val dayCount: Int)
data class MealTypeKcal(val mealType: String, val kcal: Double)

private const val MY_KCAL_SQL =
    "f.kcalPerUnit * f.quantity / (CASE WHEN f.isShared THEN MAX(m.peopleCount, 1) ELSE 1 END)"

@Dao
interface PhotoDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(photo: Photo)

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertIgnore(photos: List<Photo>)

    @Update
    suspend fun update(photo: Photo)

    @Delete
    suspend fun delete(photo: Photo)

    @Query("SELECT * FROM Photo WHERE id = :id")
    suspend fun byId(id: String): Photo?

    @Query("SELECT * FROM Photo WHERE mealId IS NULL ORDER BY takenAt ASC")
    fun unassigned(): Flow<List<Photo>>

    @Query("SELECT COUNT(*) FROM Photo WHERE mealId IS NULL")
    fun unassignedCount(): Flow<Int>

    @Query("SELECT * FROM Photo WHERE mealId = :mealId ORDER BY takenAt ASC")
    suspend fun byMeal(mealId: String): List<Photo>

    @Query("UPDATE Photo SET mealId = :mealId WHERE id IN (:photoIds)")
    suspend fun assignToMeal(photoIds: List<String>, mealId: String?)

    @Query("UPDATE Photo SET mealId = NULL WHERE id = :photoId")
    suspend fun unassign(photoId: String)

    @Query("SELECT * FROM Photo")
    suspend fun all(): List<Photo>

    @Query("SELECT * FROM Photo WHERE takenAt < :before AND mealId IS NOT NULL")
    suspend fun olderThan(before: Long): List<Photo>
}

@Dao
interface MealDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(meal: Meal)

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertIgnore(meals: List<Meal>)

    @Update
    suspend fun update(meal: Meal)

    @Query("DELETE FROM Meal WHERE id = :id")
    suspend fun deleteById(id: String)

    @Query("SELECT * FROM Meal WHERE id = :id")
    suspend fun byId(id: String): Meal?

    @Transaction
    @Query("SELECT * FROM Meal WHERE id = :id")
    fun withItemsFlow(id: String): Flow<MealWithItems?>

    @Transaction
    @Query(
        """SELECT * FROM Meal
           WHERE date(eatenAt/1000,'unixepoch','localtime') = :day
           ORDER BY eatenAt ASC"""
    )
    fun byDay(day: String): Flow<List<MealWithItems>>

    @Query("SELECT * FROM Meal")
    suspend fun all(): List<Meal>
}

@Dao
interface FoodItemDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(item: FoodItem)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(items: List<FoodItem>)

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertIgnore(items: List<FoodItem>)

    @Update
    suspend fun update(item: FoodItem)

    @Delete
    suspend fun delete(item: FoodItem)

    @Query("DELETE FROM FoodItem WHERE mealId = :mealId")
    suspend fun deleteByMeal(mealId: String)

    @Query("SELECT * FROM FoodItem WHERE mealId = :mealId ORDER BY sortOrder ASC")
    suspend fun byMeal(mealId: String): List<FoodItem>

    @Query("SELECT COALESCE(MAX(sortOrder), -1) + 1 FROM FoodItem WHERE mealId = :mealId")
    suspend fun nextSortOrder(mealId: String): Int

    @Query("SELECT * FROM FoodItem")
    suspend fun all(): List<FoodItem>
}

@Dao
interface ProductDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(product: Product)

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertIgnore(products: List<Product>)

    @Query("SELECT * FROM Product WHERE barcode = :barcode")
    suspend fun byBarcode(barcode: String): Product?

    @Query("SELECT * FROM Product")
    suspend fun all(): List<Product>
}

@Dao
interface StatsDao {
    /** 오늘(또는 임의 날짜)의 내 몫 총 열량 — REVIEWED만 */
    @Query(
        """SELECT COALESCE(SUM($MY_KCAL_SQL), 0)
           FROM FoodItem f JOIN Meal m ON f.mealId = m.id
           WHERE m.status = 'REVIEWED'
             AND date(m.eatenAt/1000,'unixepoch','localtime') = :day"""
    )
    fun dayTotal(day: String): Flow<Double>

    /** 날짜별 합계 (기간 내, 기록 있는 날만) */
    @Query(
        """SELECT date(m.eatenAt/1000,'unixepoch','localtime') AS day,
                  SUM($MY_KCAL_SQL) AS kcal
           FROM FoodItem f JOIN Meal m ON f.mealId = m.id
           WHERE m.status = 'REVIEWED' AND m.eatenAt BETWEEN :fromMillis AND :toMillis
           GROUP BY day ORDER BY day ASC"""
    )
    suspend fun dailyTotals(fromMillis: Long, toMillis: Long): List<DayKcal>

    /** 주별: ISO 주 단위 합계 + 기록 일수 (평균 = kcal/dayCount) */
    @Query(
        """SELECT strftime('%Y-W%W', m.eatenAt/1000, 'unixepoch', 'localtime') AS period,
                  SUM($MY_KCAL_SQL) AS kcal,
                  COUNT(DISTINCT date(m.eatenAt/1000,'unixepoch','localtime')) AS dayCount
           FROM FoodItem f JOIN Meal m ON f.mealId = m.id
           WHERE m.status = 'REVIEWED' AND m.eatenAt BETWEEN :fromMillis AND :toMillis
           GROUP BY period ORDER BY period ASC"""
    )
    suspend fun weeklyTotals(fromMillis: Long, toMillis: Long): List<PeriodKcal>

    /** 월별 합계 + 기록 일수 */
    @Query(
        """SELECT strftime('%Y-%m', m.eatenAt/1000, 'unixepoch', 'localtime') AS period,
                  SUM($MY_KCAL_SQL) AS kcal,
                  COUNT(DISTINCT date(m.eatenAt/1000,'unixepoch','localtime')) AS dayCount
           FROM FoodItem f JOIN Meal m ON f.mealId = m.id
           WHERE m.status = 'REVIEWED' AND m.eatenAt BETWEEN :fromMillis AND :toMillis
           GROUP BY period ORDER BY period ASC"""
    )
    suspend fun monthlyTotals(fromMillis: Long, toMillis: Long): List<PeriodKcal>

    /** 끼니 유형별 합계 */
    @Query(
        """SELECT m.mealType AS mealType, SUM($MY_KCAL_SQL) AS kcal
           FROM FoodItem f JOIN Meal m ON f.mealId = m.id
           WHERE m.status = 'REVIEWED' AND m.eatenAt BETWEEN :fromMillis AND :toMillis
           GROUP BY m.mealType"""
    )
    suspend fun byMealType(fromMillis: Long, toMillis: Long): List<MealTypeKcal>
}
