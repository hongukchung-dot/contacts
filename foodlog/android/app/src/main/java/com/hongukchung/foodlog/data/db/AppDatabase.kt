package com.hongukchung.foodlog.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverters

@Database(
    entities = [Photo::class, Meal::class, FoodItem::class, Product::class],
    version = 1,
    exportSchema = false,
)
@TypeConverters(Converters::class)
abstract class AppDatabase : RoomDatabase() {
    abstract fun photoDao(): PhotoDao
    abstract fun mealDao(): MealDao
    abstract fun foodItemDao(): FoodItemDao
    abstract fun productDao(): ProductDao
    abstract fun statsDao(): StatsDao

    companion object {
        const val SCHEMA_VERSION = 1

        fun build(context: Context): AppDatabase =
            Room.databaseBuilder(context, AppDatabase::class.java, "foodlog.db")
                .build()
    }
}
