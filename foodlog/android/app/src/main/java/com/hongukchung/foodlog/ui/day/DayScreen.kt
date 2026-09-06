package com.hongukchung.foodlog.ui.day

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.navigation.NavHostController
import com.hongukchung.foodlog.FoodLogApp
import com.hongukchung.foodlog.ui.PhotoThumb
import com.hongukchung.foodlog.ui.Routes
import com.hongukchung.foodlog.ui.appViewModel
import com.hongukchung.foodlog.util.formatKcal
import com.hongukchung.foodlog.util.label
import com.hongukchung.foodlog.util.toTimeString
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn

class DayViewModel(appContainer: FoodLogApp, day: String) : ViewModel() {
    val meals = appContainer.database.mealDao().byDay(day)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DayScreen(nav: NavHostController, day: String) {
    val vm = appViewModel(key = "day-$day") { DayViewModel(it, day) }
    val meals by vm.meals.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(day) },
                navigationIcon = {
                    IconButton(onClick = { nav.popBackStack() }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "뒤로")
                    }
                },
            )
        },
    ) { padding ->
        if (meals.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("기록이 없습니다", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            return@Scaffold
        }
        LazyColumn(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                val total = meals.sumOf { it.myTotalKcal() }
                Text(
                    "합계 ${formatKcal(total)}",
                    style = MaterialTheme.typography.titleLarge,
                    modifier = Modifier.padding(vertical = 8.dp),
                )
            }
            items(meals, key = { it.meal.id }) { data ->
                Card(
                    Modifier
                        .fillMaxWidth()
                        .clickable { nav.navigate(Routes.meal(data.meal.id)) },
                ) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        data.photos.firstOrNull()?.let {
                            PhotoThumb(it, size = 56.dp)
                            Spacer(Modifier.width(12.dp))
                        }
                        Column(Modifier.weight(1f)) {
                            Text(
                                "${data.meal.mealType.label()} · ${data.meal.eatenAt.toTimeString()}",
                                style = MaterialTheme.typography.titleSmall,
                            )
                            Text(
                                data.items.joinToString(", ") { it.name }.ifBlank { "항목 없음" },
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = 1,
                            )
                        }
                        Text(formatKcal(data.myTotalKcal()), style = MaterialTheme.typography.titleSmall)
                    }
                }
            }
        }
    }
}
