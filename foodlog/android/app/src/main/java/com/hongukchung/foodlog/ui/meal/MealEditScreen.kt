package com.hongukchung.foodlog.ui.meal

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Receipt
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.navigation.NavHostController
import com.hongukchung.foodlog.data.db.FoodItem
import com.hongukchung.foodlog.data.db.MealType
import com.hongukchung.foodlog.data.db.Product
import com.hongukchung.foodlog.data.db.myKcal
import com.hongukchung.foodlog.data.db.totalKcal
import com.hongukchung.foodlog.ui.PhotoThumb
import com.hongukchung.foodlog.ui.Routes
import com.hongukchung.foodlog.ui.appViewModel
import com.hongukchung.foodlog.ui.theme.WarningOrange
import com.hongukchung.foodlog.util.formatKcal
import com.hongukchung.foodlog.util.label
import com.hongukchung.foodlog.util.toDateTimeString
import com.hongukchung.foodlog.util.toLocalDateTime
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MealEditScreen(nav: NavHostController, mealId: String) {
    val vm = appViewModel(key = "meal-$mealId") { MealEditViewModel(it, mealId) }
    val data by vm.mealWithItems.collectAsState()
    val analyzing by vm.analyzing.collectAsState()
    val messageState by vm.message.collectAsState()
    val pendingAnalysis by vm.pendingAnalysis.collectAsState()
    val snackbar = remember { SnackbarHostState() }

    var editingItem by remember { mutableStateOf<FoodItem?>(null) }
    var showManualDialog by remember { mutableStateOf(false) }
    var showTimeDialog by remember { mutableStateOf(false) }
    var showDeleteDialog by remember { mutableStateOf(false) }
    var labelProduct by remember { mutableStateOf<Product?>(null) }

    LaunchedEffect(messageState) {
        messageState?.let {
            snackbar.showSnackbar(it)
            vm.consumeMessage()
        }
    }

    val photoPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(10),
    ) { uris -> if (uris.isNotEmpty()) vm.importPhotos(uris) }

    val labelPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        uri?.let { vm.analyzeLabel(it) { product -> labelProduct = product } }
    }

    val meal = data?.meal

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text("끼니 편집") },
                navigationIcon = {
                    IconButton(onClick = { nav.popBackStack() }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "뒤로")
                    }
                },
                actions = {
                    IconButton(onClick = { showDeleteDialog = true }) {
                        Icon(Icons.Default.Delete, contentDescription = "끼니 삭제")
                    }
                },
            )
        },
        bottomBar = {
            data?.let { d ->
                Column(Modifier.padding(16.dp)) {
                    val total = d.totalKcal()
                    val mine = d.myTotalKcal()
                    Row {
                        Text("끼니 전체 ${formatKcal(total)}", style = MaterialTheme.typography.bodyMedium)
                        Spacer(Modifier.weight(1f))
                        Text(
                            "내 몫 ${formatKcal(mine)}",
                            style = MaterialTheme.typography.titleMedium,
                        )
                    }
                    if (d.meal.peopleCount > 1) {
                        Text(
                            "공유 항목은 1/${d.meal.peopleCount} 적용",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Spacer(Modifier.height(8.dp))
                    Button(
                        onClick = { vm.save { nav.popBackStack() } },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = meal != null,
                    ) { Text("저장") }
                }
            }
        },
    ) { padding ->
        if (data == null) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }
        val d = data!!

        LazyColumn(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // 사진 스트립
            item {
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(d.photos, key = { it.id }) { photo ->
                        Box {
                            PhotoThumb(photo, size = 88.dp)
                            IconButton(
                                onClick = { vm.removePhoto(photo) },
                                modifier = Modifier
                                    .align(Alignment.TopEnd)
                                    .size(24.dp),
                            ) {
                                Icon(
                                    Icons.Default.Close,
                                    contentDescription = "사진 제거",
                                    modifier = Modifier.size(16.dp),
                                )
                            }
                        }
                    }
                    item {
                        OutlinedButton(
                            onClick = {
                                photoPicker.launch(
                                    PickVisualMediaRequest(
                                        ActivityResultContracts.PickVisualMedia.ImageOnly,
                                    ),
                                )
                            },
                            modifier = Modifier.size(88.dp),
                        ) { Icon(Icons.Default.Add, contentDescription = "사진 추가") }
                    }
                }
            }

            // 끼니 시각 / 유형 / 인원수
            item {
                meal?.let { m ->
                    Column {
                        Text(
                            m.eatenAt.toDateTimeString(),
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.clickable { showTimeDialog = true },
                            textDecoration = TextDecoration.Underline,
                        )
                        Spacer(Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            MealType.entries.forEach { type ->
                                FilterChip(
                                    selected = m.mealType == type,
                                    onClick = { vm.setMealType(type) },
                                    label = { Text(type.label()) },
                                )
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("인원수", style = MaterialTheme.typography.bodyMedium)
                            Spacer(Modifier.width(12.dp))
                            IconButton(onClick = { vm.setPeopleCount(m.peopleCount - 1) }) {
                                Icon(Icons.Default.Remove, contentDescription = "인원 감소")
                            }
                            Text("${m.peopleCount}명", style = MaterialTheme.typography.titleMedium)
                            IconButton(onClick = { vm.setPeopleCount(m.peopleCount + 1) }) {
                                Icon(Icons.Default.Add, contentDescription = "인원 증가")
                            }
                            Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }

            // AI 분석
            item {
                Button(
                    onClick = { vm.analyze() },
                    enabled = !analyzing && d.photos.isNotEmpty(),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (analyzing) {
                        CircularProgressIndicator(Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("분석 중…")
                    } else {
                        Icon(Icons.Default.AutoAwesome, contentDescription = null)
                        Spacer(Modifier.width(8.dp))
                        Text("AI 분석")
                    }
                }
            }

            // 항목 목록
            items(d.items, key = { it.id }) { item ->
                FoodItemRow(
                    item = item,
                    peopleCount = d.meal.peopleCount,
                    onClick = { editingItem = item },
                    onQuantity = { q -> vm.updateItem(item.copy(quantity = q)) },
                    onShared = { s -> vm.updateItem(item.copy(isShared = s)) },
                )
            }

            // 항목 추가
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { nav.navigate(Routes.barcode(mealId)) }) {
                        Icon(Icons.Default.QrCodeScanner, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text("바코드")
                    }
                    OutlinedButton(onClick = { showManualDialog = true }) {
                        Icon(Icons.Default.Add, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text("직접 입력")
                    }
                    OutlinedButton(onClick = {
                        labelPicker.launch(
                            PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                        )
                    }) {
                        Icon(Icons.Default.Receipt, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text("성분표")
                    }
                }
            }

            meal?.note?.let { note ->
                item {
                    Text(
                        note,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            item { Spacer(Modifier.height(24.dp)) }
        }
    }

    // ------------------------------------------------------------------ 다이얼로그·시트
    pendingAnalysis?.let { result ->
        AlertDialog(
            onDismissRequest = { vm.pendingAnalysis.value = null },
            title = { Text("분석 결과 적용") },
            text = { Text("기존 항목이 ${data?.items?.size ?: 0}개 있습니다. 분석된 ${result.items.size}개 항목을 어떻게 적용할까요?") },
            confirmButton = {
                TextButton(onClick = { vm.applyAnalysis(result, replace = false) }) { Text("병합") }
            },
            dismissButton = {
                TextButton(onClick = { vm.applyAnalysis(result, replace = true) }) { Text("교체") }
            },
        )
    }

    if (showDeleteDialog) {
        AlertDialog(
            onDismissRequest = { showDeleteDialog = false },
            title = { Text("끼니 삭제") },
            text = { Text("이 끼니를 삭제할까요? 사진은 미분석 사진함으로 돌아갑니다.") },
            confirmButton = {
                TextButton(onClick = {
                    showDeleteDialog = false
                    vm.deleteMeal { nav.popBackStack() }
                }) { Text("삭제") }
            },
            dismissButton = {
                TextButton(onClick = { showDeleteDialog = false }) { Text("취소") }
            },
        )
    }

    if (showManualDialog) {
        ManualItemDialog(
            onDismiss = { showManualDialog = false },
            onConfirm = { name, kcal, portion ->
                showManualDialog = false
                vm.addManualItem(name, kcal, portion)
            },
        )
    }

    if (showTimeDialog && meal != null) {
        EatenAtDialog(
            initial = meal.eatenAt,
            onDismiss = { showTimeDialog = false },
            onConfirm = { millis ->
                showTimeDialog = false
                vm.setEatenAt(millis)
            },
        )
    }

    labelProduct?.let { product ->
        ProductConfirmSheet(
            product = product,
            onDismiss = { labelProduct = null },
            onConfirm = { quantity ->
                vm.addItemFromProduct(product, quantity)
                labelProduct = null
            },
        )
    }

    editingItem?.let { item ->
        ItemEditSheet(
            item = item,
            onDismiss = { editingItem = null },
            onSave = { updated ->
                vm.updateItem(updated)
                editingItem = null
            },
            onDelete = {
                vm.deleteItem(item)
                editingItem = null
            },
        )
    }
}

// ---------------------------------------------------------------------------
@Composable
private fun FoodItemRow(
    item: FoodItem,
    peopleCount: Int,
    onClick: () -> Unit,
    onQuantity: (Double) -> Unit,
    onShared: (Boolean) -> Unit,
) {
    val lowConfidence = (item.confidence ?: 1f) < 0.5f
    Card(Modifier
        .fillMaxWidth()
        .clickable(onClick = onClick)) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(item.name, style = MaterialTheme.typography.titleSmall)
                    item.portionDesc?.let {
                        Text(
                            it,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (lowConfidence) {
                        Text(
                            "신뢰도 낮음 — 확인 필요",
                            style = MaterialTheme.typography.labelSmall,
                            color = WarningOrange,
                        )
                    }
                }
                Text(
                    formatKcal(item.myKcal(peopleCount)),
                    style = MaterialTheme.typography.titleMedium,
                )
            }
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { onQuantity((item.quantity - 0.5).coerceAtLeast(0.5)) }) {
                    Icon(Icons.Default.Remove, contentDescription = "배수 감소")
                }
                Text("×${trimZero(item.quantity)}", style = MaterialTheme.typography.bodyMedium)
                IconButton(onClick = { onQuantity(item.quantity + 0.5) }) {
                    Icon(Icons.Default.Add, contentDescription = "배수 증가")
                }
                Spacer(Modifier.weight(1f))
                Text("나눠 먹음", style = MaterialTheme.typography.labelMedium)
                Spacer(Modifier.width(4.dp))
                Switch(checked = item.isShared, onCheckedChange = onShared)
            }
        }
    }
}

private fun trimZero(v: Double): String =
    if (v == v.toLong().toDouble()) v.toLong().toString() else v.toString()

// ---------------------------------------------------------------------------
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ItemEditSheet(
    item: FoodItem,
    onDismiss: () -> Unit,
    onSave: (FoodItem) -> Unit,
    onDelete: () -> Unit,
) {
    var name by remember { mutableStateOf(item.name) }
    var kcal by remember { mutableStateOf(trimZero(item.kcalPerUnit)) }
    var portion by remember { mutableStateOf(item.portionDesc.orEmpty()) }
    var carbs by remember { mutableStateOf(item.carbsG?.let(::trimZero).orEmpty()) }
    var protein by remember { mutableStateOf(item.proteinG?.let(::trimZero).orEmpty()) }
    var fat by remember { mutableStateOf(item.fatG?.let(::trimZero).orEmpty()) }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("항목 수정", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("이름") })
            OutlinedTextField(
                value = portion, onValueChange = { portion = it },
                label = { Text("분량 설명 (예: 1인분(약 300g))") },
            )
            OutlinedTextField(
                value = kcal, onValueChange = { kcal = it },
                label = { Text("열량 (kcal, 배수 1 기준)") },
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = carbs, onValueChange = { carbs = it },
                    label = { Text("탄수화물 g") }, modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = protein, onValueChange = { protein = it },
                    label = { Text("단백질 g") }, modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = fat, onValueChange = { fat = it },
                    label = { Text("지방 g") }, modifier = Modifier.weight(1f),
                )
            }
            Row {
                TextButton(onClick = onDelete) {
                    Text("삭제", color = MaterialTheme.colorScheme.error)
                }
                Spacer(Modifier.weight(1f))
                Button(
                    onClick = {
                        val kcalValue = kcal.toDoubleOrNull() ?: item.kcalPerUnit
                        onSave(
                            item.copy(
                                name = name.ifBlank { item.name },
                                portionDesc = portion.ifBlank { null },
                                kcalPerUnit = kcalValue,
                                carbsG = carbs.toDoubleOrNull(),
                                proteinG = protein.toDoubleOrNull(),
                                fatG = fat.toDoubleOrNull(),
                            ),
                        )
                    },
                ) { Text("저장") }
            }
        }
    }
}

// ---------------------------------------------------------------------------
@Composable
private fun ManualItemDialog(
    onDismiss: () -> Unit,
    onConfirm: (name: String, kcal: Double, portionDesc: String?) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var kcal by remember { mutableStateOf("") }
    var portion by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("직접 입력") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("이름") })
                OutlinedTextField(value = kcal, onValueChange = { kcal = it }, label = { Text("열량 (kcal)") })
                OutlinedTextField(
                    value = portion, onValueChange = { portion = it },
                    label = { Text("분량 설명 (선택)") },
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    val kcalValue = kcal.toDoubleOrNull()
                    if (name.isNotBlank() && kcalValue != null) {
                        onConfirm(name.trim(), kcalValue, portion.ifBlank { null })
                    }
                },
            ) { Text("추가") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("취소") } },
    )
}

// ---------------------------------------------------------------------------
@Composable
private fun EatenAtDialog(
    initial: Long,
    onDismiss: () -> Unit,
    onConfirm: (Long) -> Unit,
) {
    val formatter = remember { DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm") }
    var text by remember { mutableStateOf(initial.toLocalDateTime().format(formatter)) }
    var error by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("끼니 시각") },
        text = {
            Column {
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it; error = false },
                    label = { Text("yyyy-MM-dd HH:mm") },
                    isError = error,
                )
                if (error) Text("형식이 올바르지 않습니다", color = MaterialTheme.colorScheme.error)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                try {
                    val dt = LocalDateTime.parse(text.trim(), formatter)
                    onConfirm(dt.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli())
                } catch (e: Exception) {
                    error = true
                }
            }) { Text("확인") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("취소") } },
    )
}

// ---------------------------------------------------------------------------
/** 바코드/성분표 결과 확인 시트 — 제품명·제공량·열량 확인 후 수량 입력 (스펙 4.5) */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProductConfirmSheet(
    product: Product,
    onDismiss: () -> Unit,
    onConfirm: (quantity: Double) -> Unit,
) {
    var quantity by remember { mutableStateOf("1") }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(product.name, style = MaterialTheme.typography.titleMedium)
            product.brand?.let {
                Text(it, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text("1회 제공량: ${product.servingDesc ?: "정보 없음"}")
            Text("열량: ${formatKcal(product.kcalPerServing)} / 1회 제공량")
            listOfNotNull(
                product.carbsG?.let { "탄수화물 ${it}g" },
                product.proteinG?.let { "단백질 ${it}g" },
                product.fatG?.let { "지방 ${it}g" },
            ).takeIf { it.isNotEmpty() }?.let {
                Text(it.joinToString(" · "), style = MaterialTheme.typography.bodySmall)
            }
            OutlinedTextField(
                value = quantity,
                onValueChange = { quantity = it },
                label = { Text("수량 (배수)") },
            )
            Button(
                onClick = { onConfirm(quantity.toDoubleOrNull() ?: 1.0) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("항목으로 추가") }
        }
    }
}
