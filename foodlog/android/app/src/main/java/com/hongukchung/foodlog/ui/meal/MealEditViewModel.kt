package com.hongukchung.foodlog.ui.meal

import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hongukchung.foodlog.FoodLogApp
import com.hongukchung.foodlog.data.db.FoodItem
import com.hongukchung.foodlog.data.db.ItemSource
import com.hongukchung.foodlog.data.db.MealStatus
import com.hongukchung.foodlog.data.db.MealType
import com.hongukchung.foodlog.data.db.Photo
import com.hongukchung.foodlog.data.db.Product
import com.hongukchung.foodlog.net.AnalyzeImageDto
import com.hongukchung.foodlog.net.AnalyzeResponseDto
import com.hongukchung.foodlog.net.ApiException
import com.hongukchung.foodlog.util.toIso8601
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.io.IOException
import java.util.UUID

class MealEditViewModel(
    private val appContainer: FoodLogApp,
    private val mealId: String,
) : ViewModel() {

    private val db = appContainer.database

    val mealWithItems = db.mealDao().withItemsFlow(mealId)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), null)

    val analyzing = MutableStateFlow(false)
    val message = MutableStateFlow<String?>(null)

    /** 분석 결과가 왔는데 기존 항목이 있으면 병합/교체 질문 (스펙 4.4) */
    val pendingAnalysis = MutableStateFlow<AnalyzeResponseDto?>(null)

    /** 사용자가 끼니 유형을 직접 골랐는지 — meal_type_guess 반영 여부 판단 */
    private var userChangedType = false

    fun consumeMessage() { message.value = null }

    // ------------------------------------------------------------------ 끼니 속성
    fun setMealType(type: MealType) {
        userChangedType = true
        updateMeal { it.copy(mealType = type) }
    }

    fun setPeopleCount(count: Int) = updateMeal { it.copy(peopleCount = count.coerceIn(1, 20)) }

    fun setEatenAt(epochMillis: Long) = updateMeal { it.copy(eatenAt = epochMillis) }

    fun setNote(note: String?) = updateMeal { it.copy(note = note?.ifBlank { null }) }

    private fun updateMeal(transform: (com.hongukchung.foodlog.data.db.Meal) -> com.hongukchung.foodlog.data.db.Meal) {
        viewModelScope.launch {
            db.mealDao().byId(mealId)?.let {
                db.mealDao().update(transform(it).copy(updatedAt = System.currentTimeMillis()))
            }
        }
    }

    // ------------------------------------------------------------------ 사진
    fun removePhoto(photo: Photo) {
        viewModelScope.launch { db.photoDao().unassign(photo.id) }
    }

    fun importPhotos(uris: List<Uri>) {
        viewModelScope.launch {
            for (uri in uris) {
                val photo = appContainer.photoStore.importFromUri(uri) ?: continue
                db.photoDao().insert(photo.copy(mealId = mealId))
            }
        }
    }

    // ------------------------------------------------------------------ AI 분석
    fun analyze(hint: String? = null) {
        val current = mealWithItems.value ?: return
        if (current.photos.isEmpty()) {
            message.value = "분석할 사진이 없습니다"
            return
        }
        viewModelScope.launch {
            analyzing.value = true
            try {
                val settings = appContainer.settings.current()
                val images = current.photos.mapNotNull { photo ->
                    appContainer.photoStore.toUploadBase64(photo, settings.imageMaxEdgePx)
                        ?.let { AnalyzeImageDto(data = it, takenAt = photo.takenAt.toIso8601()) }
                }
                if (images.isEmpty()) {
                    message.value = "사진 파일을 읽을 수 없습니다"
                    return@launch
                }
                val result = appContainer.api.analyzeMeal(images, hint)
                if (current.items.isEmpty()) {
                    applyAnalysis(result, replace = false)
                } else {
                    pendingAnalysis.value = result
                }
            } catch (e: ApiException) {
                message.value = e.message
            } catch (e: IOException) {
                message.value = "서버에 연결할 수 없습니다"
            } finally {
                analyzing.value = false
            }
        }
    }

    fun applyAnalysis(result: AnalyzeResponseDto, replace: Boolean) {
        pendingAnalysis.value = null
        viewModelScope.launch {
            if (replace) db.foodItemDao().deleteByMeal(mealId)
            var order = db.foodItemDao().nextSortOrder(mealId)
            val items = result.items.map { dto ->
                FoodItem(
                    id = UUID.randomUUID().toString(),
                    mealId = mealId,
                    name = dto.name,
                    source = ItemSource.PHOTO_AI,
                    portionDesc = dto.portionDesc,
                    quantity = 1.0,
                    kcalPerUnit = dto.kcal,
                    carbsG = dto.carbsG,
                    proteinG = dto.proteinG,
                    fatG = dto.fatG,
                    // likely_shared는 초기값으로만 쓰고 사용자가 토글로 확정 (스펙 6절)
                    isShared = dto.likelyShared,
                    confidence = dto.confidence?.toFloat(),
                    barcode = null,
                    sortOrder = order++,
                )
            }
            db.foodItemDao().insertAll(items)

            // meal_type_guess는 사용자가 아직 유형을 안 골랐을 때만 반영
            if (!userChangedType) {
                result.mealTypeGuess?.let { guess ->
                    runCatching { MealType.valueOf(guess) }.getOrNull()?.let { type ->
                        db.mealDao().byId(mealId)?.let {
                            db.mealDao().update(it.copy(mealType = type))
                        }
                    }
                }
            }
            if (!result.notes.isNullOrBlank()) {
                db.mealDao().byId(mealId)?.let {
                    if (it.note.isNullOrBlank()) db.mealDao().update(it.copy(note = result.notes))
                }
            }
            appContainer.mealRepository.touchMeal(mealId)
        }
    }

    // ------------------------------------------------------------------ 항목
    fun addManualItem(name: String, kcal: Double, portionDesc: String?) {
        viewModelScope.launch {
            db.foodItemDao().insert(
                FoodItem(
                    id = UUID.randomUUID().toString(),
                    mealId = mealId,
                    name = name,
                    source = ItemSource.MANUAL,
                    portionDesc = portionDesc,
                    kcalPerUnit = kcal,
                    sortOrder = db.foodItemDao().nextSortOrder(mealId),
                ),
            )
            appContainer.mealRepository.touchMeal(mealId)
        }
    }

    fun updateItem(item: FoodItem) {
        viewModelScope.launch {
            db.foodItemDao().update(item)
            appContainer.mealRepository.touchMeal(mealId)
        }
    }

    fun deleteItem(item: FoodItem) {
        viewModelScope.launch { db.foodItemDao().delete(item) }
    }

    // ------------------------------------------------------------------ 영양성분표 판독
    fun analyzeLabel(uri: Uri, onDone: (Product?) -> Unit) {
        viewModelScope.launch {
            analyzing.value = true
            try {
                val photo = appContainer.photoStore.importFromUri(uri)
                if (photo == null) {
                    message.value = "이미지를 읽을 수 없습니다"
                    onDone(null)
                    return@launch
                }
                val settings = appContainer.settings.current()
                val b64 = appContainer.photoStore.toUploadBase64(photo, settings.imageMaxEdgePx)
                // 라벨 사진은 기록용 사진이 아니므로 저장소에서 제거
                appContainer.photoStore.deleteFiles(photo)
                if (b64 == null) {
                    message.value = "이미지를 읽을 수 없습니다"
                    onDone(null)
                    return@launch
                }
                val label = appContainer.api.analyzeLabel(AnalyzeImageDto(data = b64))
                val kcal = label.kcalPerServing
                if (label.productName.isNullOrBlank() || kcal == null) {
                    message.value = "영양성분표를 읽지 못했습니다"
                    onDone(null)
                    return@launch
                }
                onDone(
                    Product(
                        barcode = "label-${UUID.randomUUID()}",
                        name = label.productName,
                        brand = null,
                        servingDesc = label.servingDesc,
                        kcalPerServing = kcal,
                        carbsG = label.carbsG,
                        proteinG = label.proteinG,
                        fatG = label.fatG,
                        source = "LABEL_AI",
                        fetchedAt = System.currentTimeMillis(),
                    ),
                )
            } catch (e: ApiException) {
                message.value = e.message
                onDone(null)
            } catch (e: IOException) {
                message.value = "서버에 연결할 수 없습니다"
                onDone(null)
            } finally {
                analyzing.value = false
            }
        }
    }

    fun addItemFromProduct(product: Product, quantity: Double) {
        viewModelScope.launch {
            appContainer.mealRepository.addItemFromProduct(mealId, product, quantity)
        }
    }

    // ------------------------------------------------------------------ 저장/삭제
    fun save(onSaved: () -> Unit) {
        viewModelScope.launch {
            db.mealDao().byId(mealId)?.let {
                db.mealDao().update(
                    it.copy(status = MealStatus.REVIEWED, updatedAt = System.currentTimeMillis()),
                )
            }
            onSaved()
        }
    }

    fun deleteMeal(onDeleted: () -> Unit) {
        viewModelScope.launch {
            appContainer.mealRepository.deleteMeal(mealId)
            onDeleted()
        }
    }
}
