package com.hongukchung.foodlog.util

import com.hongukchung.foodlog.data.db.MealType
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

val DAY_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")
val TIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")
val DATETIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("M월 d일 (E) HH:mm")

fun Long.toLocalDateTime(): LocalDateTime =
    LocalDateTime.ofInstant(Instant.ofEpochMilli(this), ZoneId.systemDefault())

fun Long.toLocalDate(): LocalDate = toLocalDateTime().toLocalDate()

fun Long.toDayString(): String = toLocalDate().format(DAY_FORMAT)

fun Long.toTimeString(): String = toLocalDateTime().format(TIME_FORMAT)

fun Long.toDateTimeString(): String = toLocalDateTime().format(DATETIME_FORMAT)

fun LocalDate.startOfDayMillis(): Long =
    atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()

fun LocalDate.endOfDayMillis(): Long = plusDays(1).startOfDayMillis() - 1

/** ISO-8601 (오프셋 포함) — 서버 taken_at 용 */
fun Long.toIso8601(): String =
    OffsetDateTime.ofInstant(Instant.ofEpochMilli(this), ZoneId.systemDefault())
        .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)

/** 시간대로 끼니 유형 자동 제안 (스펙 3절) */
fun suggestMealType(epochMillis: Long): MealType {
    return when (epochMillis.toLocalDateTime().hour) {
        in 4..9 -> MealType.BREAKFAST
        in 10..14 -> MealType.LUNCH
        in 17..21 -> MealType.DINNER
        else -> MealType.SNACK
    }
}

fun MealType.label(): String = when (this) {
    MealType.BREAKFAST -> "아침"
    MealType.LUNCH -> "점심"
    MealType.DINNER -> "저녁"
    MealType.SNACK -> "간식"
}

fun formatKcal(kcal: Double): String = "%,d kcal".format(kcal.toInt())
