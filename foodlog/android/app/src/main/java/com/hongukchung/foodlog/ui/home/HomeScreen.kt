package com.hongukchung.foodlog.ui.home

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.navigation.NavHostController
import com.hongukchung.foodlog.FoodLogApp
import com.hongukchung.foodlog.data.db.MealStatus
import com.hongukchung.foodlog.data.db.MealWithItems
import com.hongukchung.foodlog.ui.PhotoThumb
import com.hongukchung.foodlog.ui.Routes
import com.hongukchung.foodlog.ui.appViewModel
import com.hongukchung.foodlog.util.formatKcal
import com.hongukchung.foodlog.util.label
import com.hongukchung.foodlog.util.toDayString
import com.hongukchung.foodlog.util.toTimeString
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class HomeViewModel(private val appContainer: FoodLogApp) : ViewModel() {
    private val today = System.currentTimeMillis().toDayString()

    val todayKcal = appContainer.database.statsDao().dayTotal(today)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0.0)

    val todayMeals = appContainer.database.mealDao().byDay(today)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val unassignedCount = appContainer.database.photoDao().unassignedCount()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    val settings = appContainer.settings.settings

    suspend fun createEmptyMeal(): String = appContainer.mealRepository.createEmptyMeal()
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(nav: NavHostController) {
    val vm = appViewModel { HomeViewModel(it) }
    val todayKcal by vm.todayKcal.collectAsState()
    val meals by vm.todayMeals.collectAsState()
    val unassigned by vm.unassignedCount.collectAsState()
    val settings by vm.settings.collectAsState(initial = null)
    val scope = rememberCoroutineScope()
    var fabExpanded by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("오늘") },
                actions = {
                    IconButton(onClick = { nav.navigate(Routes.STATS) }) {
                        Icon(Icons.Default.BarChart, contentDescription = "통계")
                    }
                    IconButton(onClick = { nav.navigate(Routes.SETTINGS) }) {
                        Icon(Icons.Default.Settings, contentDescription = "설정")
                    }
                },
            )
        },
        floatingActionButton = {
            Column(horizontalAlignment = Alignment.End) {
                if (fabExpanded) {
                    SmallFloatingActionButton(onClick = {
                        fabExpanded = false
                        scope.launch { nav.navigate(Routes.meal(vm.createEmptyMeal())) }
                    }) { Icon(Icons.Default.Edit, contentDescription = "직접 입력") }
                    Spacer(Modifier.height(12.dp))
                    SmallFloatingActionButton(onClick = {
                        fabExpanded = false
                        scope.launch { nav.navigate(Routes.barcode(vm.createEmptyMeal())) }
                    }) { Icon(Icons.Default.QrCodeScanner, contentDescription = "바코드") }
                    Spacer(Modifier.height(12.dp))
                    SmallFloatingActionButton(onClick = {
                        fabExpanded = false
                        nav.navigate(Routes.CAPTURE)
                    }) { Icon(Icons.Default.PhotoCamera, contentDescription = "카메라") }
                    Spacer(Modifier.height(12.dp))
                }
                FloatingActionButton(onClick = { fabExpanded = !fabExpanded }) {
                    Icon(
                        if (fabExpanded) Icons.Default.PhotoCamera else Icons.Default.Add,
                        contentDescription = "추가",
                    )
                }
            }
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                val goal = settings?.dailyGoalKcal ?: 2000
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Text("오늘 섭취", style = MaterialTheme.typography.labelMedium)
                        Row(verticalAlignment = Alignment.Bottom) {
                            Text(
                                formatKcal(todayKcal),
                                style = MaterialTheme.typography.headlineMedium,
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(
                                "/ 목표 ${formatKcal(goal.toDouble())}",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Spacer(Modifier.height(8.dp))
                        LinearProgressIndicator(
                            progress = {
                                (todayKcal / goal.coerceAtLeast(1)).toFloat().coerceIn(0f, 1f)
                            },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }

            if (unassigned > 0) {
                item {
                    AssistChip(
                        onClick = { nav.navigate(Routes.INBOX) },
                        label = { Text("미분석 사진 ${unassigned}장 — 끼니로 정리하기") },
                    )
                }
            }

            items(meals, key = { it.meal.id }) { mealWithItems ->
                MealCard(mealWithItems) { nav.navigate(Routes.meal(mealWithItems.meal.id)) }
            }

            if (meals.isEmpty()) {
                item {
                    Text(
                        "아직 기록이 없습니다. 우측 하단 버튼으로 사진을 찍어보세요.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 24.dp),
                    )
                }
            }

            item { Spacer(Modifier.height(80.dp)) }
        }
    }
}

@Composable
private fun MealCard(data: MealWithItems, onClick: () -> Unit) {
    val meal = data.meal
    Card(modifier = Modifier
        .fillMaxWidth()
        .clickable(onClick = onClick)) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            data.photos.firstOrNull()?.let {
                PhotoThumb(it, size = 64.dp)
                Spacer(Modifier.width(12.dp))
            }
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(meal.mealType.label(), style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        meal.eatenAt.toTimeString(),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (meal.status != MealStatus.REVIEWED) {
                        Spacer(Modifier.width(8.dp))
                        Text(
                            "확인 필요",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
                val summary = data.items.joinToString(", ") { it.name }
                    .ifBlank { "항목 없음" }
                Text(
                    summary,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1,
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                formatKcal(data.myTotalKcal()),
                style = MaterialTheme.typography.titleMedium,
            )
        }
    }
}
