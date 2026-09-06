package com.hongukchung.foodlog.ui.barcode

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ViewModel
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewModelScope
import androidx.navigation.NavHostController
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.hongukchung.foodlog.FoodLogApp
import com.hongukchung.foodlog.data.db.Product
import com.hongukchung.foodlog.net.ApiException
import com.hongukchung.foodlog.ui.appViewModel
import com.hongukchung.foodlog.ui.meal.ProductConfirmSheet
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.launch
import java.io.IOException
import java.util.concurrent.Executors

sealed interface ScanState {
    data object Scanning : ScanState
    data class Loading(val code: String) : ScanState
    data class Found(val product: Product, val source: String) : ScanState
    data class NotFound(val code: String) : ScanState
    data class Error(val code: String, val message: String) : ScanState
}

class BarcodeViewModel(private val appContainer: FoodLogApp) : ViewModel() {
    val state = MutableStateFlow<ScanState>(ScanState.Scanning)

    fun onBarcode(code: String) {
        if (state.value !is ScanState.Scanning) return
        state.value = ScanState.Loading(code)
        viewModelScope.launch {
            // 1) 로컬 Product 캐시
            appContainer.database.productDao().byBarcode(code)?.let {
                state.value = ScanState.Found(it, it.source)
                return@launch
            }
            // 2) 서버 조회
            try {
                val response = appContainer.api.lookupBarcode(code)
                val dto = response.product
                if (response.found && dto != null) {
                    val product = Product(
                        barcode = dto.barcode,
                        name = dto.name,
                        brand = dto.brand,
                        servingDesc = dto.servingDesc,
                        kcalPerServing = dto.kcalPerServing,
                        carbsG = dto.carbsG,
                        proteinG = dto.proteinG,
                        fatG = dto.fatG,
                        source = response.source ?: "OFF",
                        fetchedAt = System.currentTimeMillis(),
                    )
                    state.value = ScanState.Found(product, product.source)
                } else {
                    state.value = ScanState.NotFound(code)
                }
            } catch (e: ApiException) {
                state.value = ScanState.Error(code, e.message ?: "조회 실패")
            } catch (e: IOException) {
                state.value = ScanState.Error(code, "서버에 연결할 수 없습니다")
            }
        }
    }

    fun addToMeal(mealId: String, product: Product, quantity: Double, onDone: () -> Unit) {
        viewModelScope.launch {
            appContainer.mealRepository.addItemFromProduct(mealId, product, quantity)
            onDone()
        }
    }

    /** 직접 입력한 제품 — Product 캐시에 저장해 다음에 재사용 (스펙 4.5) */
    fun manualProduct(code: String, name: String, kcal: Double, servingDesc: String?): Product =
        Product(
            barcode = code,
            name = name,
            servingDesc = servingDesc,
            kcalPerServing = kcal,
            source = "MANUAL",
            fetchedAt = System.currentTimeMillis(),
        )

    fun resume() {
        state.value = ScanState.Scanning
    }
}

@OptIn(ExperimentalGetImage::class)
@Composable
fun BarcodeScreen(nav: NavHostController, mealId: String) {
    val vm = appViewModel { BarcodeViewModel(it) }
    val state by vm.state.collectAsState()
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> hasPermission = granted }

    var manualEntryFor by remember { mutableStateOf<String?>(null) }

    val executor = remember { Executors.newSingleThreadExecutor() }
    val scanner = remember {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder()
                .setBarcodeFormats(
                    Barcode.FORMAT_EAN_13,
                    Barcode.FORMAT_EAN_8,
                    Barcode.FORMAT_UPC_A,
                    Barcode.FORMAT_UPC_E,
                    Barcode.FORMAT_CODE_128,
                )
                .build(),
        )
    }
    DisposableEffect(Unit) {
        onDispose {
            executor.shutdown()
            scanner.close()
        }
    }

    Box(Modifier.fillMaxSize()) {
        if (hasPermission) {
            AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { ctx ->
                    PreviewView(ctx).also { previewView ->
                        val analysis = ImageAnalysis.Builder()
                            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                            .build()
                        analysis.setAnalyzer(executor) { imageProxy: ImageProxy ->
                            val mediaImage = imageProxy.image
                            if (mediaImage == null) {
                                imageProxy.close()
                                return@setAnalyzer
                            }
                            val input = InputImage.fromMediaImage(
                                mediaImage,
                                imageProxy.imageInfo.rotationDegrees,
                            )
                            scanner.process(input)
                                .addOnSuccessListener { barcodes ->
                                    barcodes.firstOrNull()?.rawValue?.let { raw ->
                                        if (raw.matches(Regex("[0-9]{6,14}"))) {
                                            vm.onBarcode(raw)
                                        }
                                    }
                                }
                                .addOnCompleteListener { imageProxy.close() }
                        }
                        lifecycleOwner.lifecycleScopeLaunch {
                            val provider = ProcessCameraProvider.getInstance(ctx).await()
                            val preview = androidx.camera.core.Preview.Builder().build().apply {
                                setSurfaceProvider(previewView.surfaceProvider)
                            }
                            provider.unbindAll()
                            provider.bindToLifecycle(
                                lifecycleOwner,
                                CameraSelector.DEFAULT_BACK_CAMERA,
                                preview,
                                analysis,
                            )
                        }
                    }
                },
            )
        } else {
            Column(
                Modifier
                    .fillMaxSize()
                    .padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("바코드를 스캔하려면 카메라 권한이 필요합니다")
                Button(onClick = { permissionLauncher.launch(Manifest.permission.CAMERA) }) {
                    Text("권한 허용")
                }
            }
        }

        IconButton(
            onClick = { nav.popBackStack() },
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(8.dp),
        ) {
            Icon(Icons.Default.ArrowBack, contentDescription = "뒤로", tint = Color.White)
        }

        Surface(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(16.dp),
            shape = MaterialTheme.shapes.medium,
        ) {
            Text(
                "제품 바코드를 화면에 비추세요 (EAN-13/8, UPC, Code 128)",
                modifier = Modifier.padding(12.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        if (state is ScanState.Loading) {
            CircularProgressIndicator(Modifier.align(Alignment.Center))
        }
    }

    when (val s = state) {
        is ScanState.Found -> {
            ProductConfirmSheet(
                product = s.product,
                onDismiss = { vm.resume() },
                onConfirm = { quantity ->
                    vm.addToMeal(mealId, s.product, quantity) { nav.popBackStack() }
                },
            )
        }

        is ScanState.NotFound, is ScanState.Error -> {
            val code = if (s is ScanState.NotFound) s.code else (s as ScanState.Error).code
            val detail = if (s is ScanState.Error) s.message else "등록된 제품 정보가 없습니다"
            AlertDialog(
                onDismissRequest = { vm.resume() },
                title = { Text("조회 실패 ($code)") },
                text = { Text("$detail\n영양성분표를 찍어서 읽거나 직접 입력할 수 있습니다. 성분표 판독은 끼니 화면의 '성분표' 버튼을 이용하세요.") },
                confirmButton = {
                    TextButton(onClick = { manualEntryFor = code; vm.resume() }) { Text("직접 입력") }
                },
                dismissButton = {
                    TextButton(onClick = { vm.resume() }) { Text("다시 스캔") }
                },
            )
        }

        else -> Unit
    }

    manualEntryFor?.let { code ->
        ManualProductDialog(
            barcode = code,
            onDismiss = { manualEntryFor = null },
            onConfirm = { name, kcal, serving, quantity ->
                val product = vm.manualProduct(code, name, kcal, serving)
                manualEntryFor = null
                vm.addToMeal(mealId, product, quantity) { nav.popBackStack() }
            },
        )
    }
}

/** ProcessCameraProvider await용 — lifecycleScope 접근 헬퍼 */
private fun LifecycleOwner.lifecycleScopeLaunch(
    block: suspend kotlinx.coroutines.CoroutineScope.() -> Unit,
) {
    lifecycleScope.launch(block = block)
}

@Composable
private fun ManualProductDialog(
    barcode: String,
    onDismiss: () -> Unit,
    onConfirm: (name: String, kcal: Double, servingDesc: String?, quantity: Double) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var kcal by remember { mutableStateOf("") }
    var serving by remember { mutableStateOf("") }
    var quantity by remember { mutableStateOf("1") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("제품 직접 입력") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("바코드 $barcode — 입력한 제품은 저장되어 다음에 재사용됩니다",
                    style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("제품명") })
                OutlinedTextField(
                    value = serving, onValueChange = { serving = it },
                    label = { Text("1회 제공량 (예: 1봉지(90g))") },
                )
                OutlinedTextField(
                    value = kcal, onValueChange = { kcal = it },
                    label = { Text("열량 (kcal / 1회 제공량)") },
                )
                OutlinedTextField(
                    value = quantity, onValueChange = { quantity = it },
                    label = { Text("수량 (배수)") },
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val kcalValue = kcal.toDoubleOrNull()
                if (name.isNotBlank() && kcalValue != null) {
                    onConfirm(
                        name.trim(), kcalValue, serving.ifBlank { null },
                        quantity.toDoubleOrNull() ?: 1.0,
                    )
                }
            }) { Text("추가") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("취소") } },
    )
}
