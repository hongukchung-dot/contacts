package com.hongukchung.foodlog.ui.stats

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.navigation.NavHostController
import com.hongukchung.foodlog.FoodLogApp
import com.hongukchung.foodlog.data.db.DayKcal
import com.hongukchung.foodlog.data.db.MealTypeKcal
import com.hongukchung.foodlog.data.db.PeriodKcal
import com.hongukchung.foodlog.data.db.MealType
import com.hongukchung.foodlog.ui.Routes
import com.hongukchung.foodlog.ui.appViewModel
import com.hongukchung.foodlog.util.endOfDayMillis
import com.hongukchung.foodlog.util.formatKcal
import com.hongukchung.foodlog.util.label
import com.hongukchung.foodlog.util.startOfDayMillis
import com.patrykandpatrick.vico.compose.cartesian.CartesianChartHost
import com.patrykandpatrick.vico.compose.cartesian.axis.rememberBottom
import com.patrykandpatrick.vico.compose.cartesian.axis.rememberStart
import com.patrykandpatrick.vico.compose.cartesian.layer.rememberColumnCartesianLayer
import com.patrykandpatrick.vico.compose.cartesian.layer.rememberLineCartesianLayer
import com.patrykandpatrick.vico.compose.cartesian.rememberCartesianChart
import com.patrykandpatrick.vico.core.cartesian.axis.HorizontalAxis
import com.patrykandpatrick.vico.core.cartesian.axis.VerticalAxis
import com.patrykandpatrick.vico.core.cartesian.data.CartesianChartModelProducer
import com.patrykandpatrick.vico.core.cartesian.data.columnSeries
import com.patrykandpatrick.vico.core.cartesian.data.lineSeries
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import java.time.LocalDate

data class StatsUiState(
    val tab: Int = 0,                       // 0=일, 1=주, 2=월
    val daily: List<DayKcal> = emptyList(),        // 최근 14일 (빈 날 0 채움)
    val movingAvg: List<Double> = emptyList(),     // 7일 이동평균
    val weekly: List<PeriodKcal> = emptyList(),    // 최근 12주
    val monthly: List<PeriodKcal> = emptyList(),   // 최근 12개월
    val byMealType: List<MealTypeKcal> = emptyList(),
    val periodAvg: Double = 0.0,
    val periodMax: Double = 0.0,
    val periodMin: Double = 0.0,
    val goalDays: Int = 0,
    val goal: Int = 2000,
)

class StatsViewModel(private val appContainer: FoodLogApp) : ViewModel() {
    val state = MutableStateFlow(StatsUiState())

    init {
        load(0)
    }

    fun load(tab: Int) {
        viewModelScope.launch {
            val stats = appContainer.database.statsDao()
            val goal = appContainer.settings.current().dailyGoalKcal
            val today = LocalDate.now()

            when (tab) {
                0 -> {
                    val from = today.minusDays(13)
                    val rows = stats.dailyTotals(from.startOfDayMillis(), today.endOfDayMillis())
                    val byDay = rows.associateBy { it.day }
                    // 빈 날은 0으로 채워 14일 연속 시리즈
                    val days = (0..13L).map { offset ->
                        val d = from.plusDays(offset)
                        byDay[d.toString()] ?: DayKcal(d.toString(), 0.0)
                    }
                    // 7일 이동평균 (해당 날짜 포함 이전 7일 창)
                    val avgSourceFrom = today.minusDays(19)
                    val wideRows = stats.dailyTotals(avgSourceFrom.startOfDayMillis(), today.endOfDayMillis())
                        .associateBy { it.day }
                    val wide = (0..19L).map { offset ->
                        wideRows[avgSourceFrom.plusDays(offset).toString()]?.kcal ?: 0.0
                    }
                    val movingAvg = (6..19).map { i -> wide.subList(i - 6, i + 1).average() }

                    val recorded = days.filter { it.kcal > 0 }
                    val mealTypes = stats.byMealType(from.startOfDayMillis(), today.endOfDayMillis())
                    state.value = state.value.copy(
                        tab = tab,
                        daily = days,
                        movingAvg = movingAvg,
                        byMealType = mealTypes,
                        periodAvg = recorded.map { it.kcal }.ifEmpty { listOf(0.0) }.average(),
                        periodMax = recorded.maxOfOrNull { it.kcal } ?: 0.0,
                        periodMin = recorded.minOfOrNull { it.kcal } ?: 0.0,
                        goalDays = recorded.count { it.kcal <= goal },
                        goal = goal,
                    )
                }

                1 -> {
                    val from = today.minusWeeks(11)
                    val rows = stats.weeklyTotals(from.startOfDayMillis(), today.endOfDayMillis())
                    val mealTypes = stats.byMealType(from.startOfDayMillis(), today.endOfDayMillis())
                    val avgs = rows.map { it.kcal / it.dayCount.coerceAtLeast(1) }
                    state.value = state.value.copy(
                        tab = tab,
                        weekly = rows,
                        byMealType = mealTypes,
                        periodAvg = avgs.ifEmpty { listOf(0.0) }.average(),
                        periodMax = avgs.maxOrNull() ?: 0.0,
                        periodMin = avgs.minOrNull() ?: 0.0,
                        goalDays = 0,
                        goal = goal,
                    )
                }

                else -> {
                    val from = today.minusMonths(11).withDayOfMonth(1)
                    val rows = stats.monthlyTotals(from.startOfDayMillis(), today.endOfDayMillis())
                    val mealTypes = stats.byMealType(from.startOfDayMillis(), today.endOfDayMillis())
                    val avgs = rows.map { it.kcal / it.dayCount.coerceAtLeast(1) }
                    state.value = state.value.copy(
                        tab = tab,
                        monthly = rows,
                        byMealType = mealTypes,
                        periodAvg = avgs.ifEmpty { listOf(0.0) }.average(),
                        periodMax = avgs.maxOrNull() ?: 0.0,
                        periodMin = avgs.minOrNull() ?: 0.0,
                        goalDays = 0,
                        goal = goal,
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StatsScreen(nav: NavHostController) {
    val vm = appViewModel { StatsViewModel(it) }
    val state by vm.state.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("통계") },
                navigationIcon = {
                    IconButton(onClick = { nav.popBackStack() }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "뒤로")
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            TabRow(selectedTabIndex = state.tab) {
                listOf("일", "주", "월").forEachIndexed { i, title ->
                    Tab(
                        selected = state.tab == i,
                        onClick = { vm.load(i) },
                        text = { Text(title) },
                    )
                }
            }

            LazyColumn(
                Modifier
                    .fillMaxSize()
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                item {
                    when (state.tab) {
                        0 -> DailyChart(state)
                        1 -> PeriodChart(state.weekly, state.goal)
                        else -> PeriodChart(state.monthly, state.goal)
                    }
                }
                item { SummaryCard(state) }
                item { MealTypeCard(state.byMealType) }
                if (state.tab == 0) {
                    item {
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text("날짜별 상세", style = MaterialTheme.typography.titleSmall)
                            state.daily.reversed().filter { it.kcal > 0 }.forEach { day ->
                                Row(
                                    Modifier
                                        .fillMaxWidth()
                                        .clickable { nav.navigate(Routes.day(day.day)) }
                                        .padding(vertical = 6.dp),
                                ) {
                                    Text(day.day, Modifier.weight(1f))
                                    Text(formatKcal(day.kcal))
                                }
                            }
                        }
                    }
                }
                item { Spacer(Modifier.height(32.dp)) }
            }
        }
    }
}

/**
 * 일 탭: 최근 14일 막대 + 7일 이동평균 선 + 목표선 (스펙 4.6)
 * ※ Vico 2.x API — 버전 호환은 스펙 10절대로 빌드 시 확인
 */
@Composable
private fun DailyChart(state: StatsUiState) {
    val producer = remember { CartesianChartModelProducer() }
    LaunchedEffect(state.daily, state.goal) {
        if (state.daily.isEmpty()) return@LaunchedEffect
        producer.runTransaction {
            columnSeries { series(state.daily.map { it.kcal }) }
            lineSeries {
                series(state.movingAvg.ifEmpty { state.daily.map { it.kcal } })
                series(state.daily.map { state.goal.toDouble() })
            }
        }
    }
    Column {
        CartesianChartHost(
            chart = rememberCartesianChart(
                rememberColumnCartesianLayer(),
                rememberLineCartesianLayer(),
                startAxis = VerticalAxis.rememberStart(),
                bottomAxis = HorizontalAxis.rememberBottom(),
            ),
            modelProducer = producer,
            modifier = Modifier
                .fillMaxWidth()
                .height(240.dp),
        )
        Row(Modifier.fillMaxWidth()) {
            Text(
                state.daily.firstOrNull()?.day?.substring(5) ?: "",
                style = MaterialTheme.typography.labelSmall,
            )
            Spacer(Modifier.weight(1f))
            Text("오늘", style = MaterialTheme.typography.labelSmall)
        }
        Text(
            "막대: 일별 섭취 · 선: 7일 이동평균 / 목표(${state.goal}kcal)",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** 주/월 탭: 기간 평균 막대 */
@Composable
private fun PeriodChart(rows: List<PeriodKcal>, goal: Int) {
    val producer = remember { CartesianChartModelProducer() }
    LaunchedEffect(rows, goal) {
        if (rows.isEmpty()) return@LaunchedEffect
        producer.runTransaction {
            columnSeries { series(rows.map { it.kcal / it.dayCount.coerceAtLeast(1) }) }
            lineSeries { series(rows.map { goal.toDouble() }) }
        }
    }
    if (rows.isEmpty()) {
        Text("기록이 없습니다", color = MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }
    Column {
        CartesianChartHost(
            chart = rememberCartesianChart(
                rememberColumnCartesianLayer(),
                rememberLineCartesianLayer(),
                startAxis = VerticalAxis.rememberStart(),
                bottomAxis = HorizontalAxis.rememberBottom(),
            ),
            modelProducer = producer,
            modifier = Modifier
                .fillMaxWidth()
                .height(240.dp),
        )
        Row(Modifier.fillMaxWidth()) {
            Text(rows.first().period, style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.weight(1f))
            Text(rows.last().period, style = MaterialTheme.typography.labelSmall)
        }
        Text(
            "막대: 하루 평균 섭취 (기록 있는 날 기준) · 선: 목표",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun SummaryCard(state: StatsUiState) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("요약", style = MaterialTheme.typography.titleSmall)
            Text("기간 평균: ${formatKcal(state.periodAvg)}")
            Text("최고: ${formatKcal(state.periodMax)} · 최저: ${formatKcal(state.periodMin)}")
            if (state.tab == 0) {
                Text("목표(${state.goal}kcal) 달성 일수: ${state.goalDays}일")
            }
        }
    }
}

@Composable
private fun MealTypeCard(rows: List<MealTypeKcal>) {
    if (rows.isEmpty()) return
    val total = rows.sumOf { it.kcal }.coerceAtLeast(1.0)
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("끼니 유형별 비중", style = MaterialTheme.typography.titleSmall)
            rows.sortedByDescending { it.kcal }.forEach { row ->
                val label = runCatching { MealType.valueOf(row.mealType).label() }
                    .getOrDefault(row.mealType)
                Row {
                    Text(label, Modifier.weight(1f))
                    Text("${formatKcal(row.kcal)} (${(row.kcal / total * 100).toInt()}%)")
                }
            }
        }
    }
}
