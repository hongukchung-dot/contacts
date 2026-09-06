package com.hongukchung.foodlog.net

import com.hongukchung.foodlog.data.Settings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

// ---------------------------------------------------------------------------
// DTO — 서버 스키마 (스펙 5·6절)
// ---------------------------------------------------------------------------
@Serializable
data class AnalyzeImageDto(
    val data: String,
    @SerialName("taken_at") val takenAt: String? = null,
)

@Serializable
data class AnalyzeRequestDto(
    val images: List<AnalyzeImageDto>,
    val hint: String? = null,
    val mode: String = "meal",
)

@Serializable
data class AnalyzeItemDto(
    val name: String,
    @SerialName("portion_desc") val portionDesc: String? = null,
    val kcal: Double = 0.0,
    @SerialName("carbs_g") val carbsG: Double? = null,
    @SerialName("protein_g") val proteinG: Double? = null,
    @SerialName("fat_g") val fatG: Double? = null,
    @SerialName("likely_shared") val likelyShared: Boolean = false,
    val confidence: Double? = null,
    @SerialName("photo_indices") val photoIndices: List<Int> = emptyList(),
)

@Serializable
data class AnalyzeResponseDto(
    val items: List<AnalyzeItemDto> = emptyList(),
    @SerialName("meal_type_guess") val mealTypeGuess: String? = null,
    val notes: String? = null,
)

@Serializable
data class LabelResponseDto(
    @SerialName("product_name") val productName: String? = null,
    @SerialName("serving_desc") val servingDesc: String? = null,
    @SerialName("total_servings") val totalServings: Double? = null,
    @SerialName("kcal_per_serving") val kcalPerServing: Double? = null,
    @SerialName("carbs_g") val carbsG: Double? = null,
    @SerialName("protein_g") val proteinG: Double? = null,
    @SerialName("fat_g") val fatG: Double? = null,
    val confidence: Double? = null,
)

@Serializable
data class BarcodeProductDto(
    val barcode: String,
    val name: String,
    val brand: String? = null,
    @SerialName("serving_desc") val servingDesc: String? = null,
    @SerialName("kcal_per_serving") val kcalPerServing: Double,
    @SerialName("carbs_g") val carbsG: Double? = null,
    @SerialName("protein_g") val proteinG: Double? = null,
    @SerialName("fat_g") val fatG: Double? = null,
)

@Serializable
data class BarcodeResponseDto(
    val found: Boolean,
    val product: BarcodeProductDto? = null,
    val source: String? = null,
)

class ApiException(val code: Int, message: String) : IOException(message)

// ---------------------------------------------------------------------------
// API 클라이언트 — 앱은 프록시 서버만 호출한다 (Anthropic 키 없음)
// ---------------------------------------------------------------------------
class FoodLogApi(private val settingsProvider: suspend () -> Settings) {

    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)   // 비전 분석은 오래 걸릴 수 있다
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private suspend fun baseUrlAndToken(): Pair<String, String> {
        val s = settingsProvider()
        val base = s.serverBaseUrl.trimEnd('/')
        if (base.isBlank()) throw ApiException(0, "서버 주소가 설정되지 않았습니다")
        if (s.appToken.isBlank()) throw ApiException(0, "앱 토큰이 설정되지 않았습니다")
        return base to s.appToken
    }

    suspend fun analyzeMeal(
        images: List<AnalyzeImageDto>,
        hint: String? = null,
    ): AnalyzeResponseDto = withContext(Dispatchers.IO) {
        val body = json.encodeToString(AnalyzeRequestDto(images = images, hint = hint, mode = "meal"))
        json.decodeFromString<AnalyzeResponseDto>(post("/v1/analyze", body))
    }

    suspend fun analyzeLabel(image: AnalyzeImageDto): LabelResponseDto = withContext(Dispatchers.IO) {
        val body = json.encodeToString(AnalyzeRequestDto(images = listOf(image), mode = "label"))
        json.decodeFromString<LabelResponseDto>(post("/v1/analyze", body))
    }

    suspend fun lookupBarcode(code: String): BarcodeResponseDto = withContext(Dispatchers.IO) {
        json.decodeFromString<BarcodeResponseDto>(get("/v1/barcode/$code"))
    }

    suspend fun health(): Boolean = withContext(Dispatchers.IO) {
        try {
            get("/v1/health")
            true
        } catch (e: IOException) {
            false
        }
    }

    // ------------------------------------------------------------------
    private suspend fun post(path: String, jsonBody: String): String {
        val (base, token) = baseUrlAndToken()
        val request = Request.Builder()
            .url(base + path)
            .header("X-App-Token", token)
            .post(jsonBody.toRequestBody("application/json".toMediaType()))
            .build()
        return execute(request)
    }

    private suspend fun get(path: String): String {
        val (base, token) = baseUrlAndToken()
        val request = Request.Builder()
            .url(base + path)
            .header("X-App-Token", token)
            .get()
            .build()
        return execute(request)
    }

    private fun execute(request: Request): String {
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val message = when (response.code) {
                    401 -> "앱 토큰이 올바르지 않습니다"
                    429 -> "일일 사용량을 초과했습니다. 내일 다시 시도하세요"
                    else -> "서버 오류 (${response.code})"
                }
                throw ApiException(response.code, message)
            }
            return body
        }
    }
}
