package com.hongukchung.foodlog.ui.inbox

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import com.hongukchung.foodlog.data.db.Photo
import com.hongukchung.foodlog.ui.PhotoThumb
import com.hongukchung.foodlog.ui.Routes
import com.hongukchung.foodlog.ui.appViewModel
import com.hongukchung.foodlog.util.toDateTimeString
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class InboxViewModel(private val appContainer: FoodLogApp) : ViewModel() {
    /** 시간 간격(90분) 기준 자동 그룹 제안 */
    val groups = appContainer.database.photoDao().unassigned()
        .map { appContainer.mealRepository.suggestGroups(it) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    suspend fun createMeal(photos: List<Photo>): String =
        appContainer.mealRepository.createMealFromPhotos(photos)

    fun deletePhoto(photo: Photo) {
        viewModelScope.launch { appContainer.mealRepository.deletePhoto(photo) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InboxScreen(nav: NavHostController) {
    val vm = appViewModel { InboxViewModel(it) }
    val groups by vm.groups.collectAsState()
    val scope = rememberCoroutineScope()

    // 그룹 경계는 사용자가 체크박스로 조정
    var selected by remember { mutableStateOf(setOf<String>()) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("미분석 사진함") },
                navigationIcon = {
                    IconButton(onClick = { nav.popBackStack() }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "뒤로")
                    }
                },
            )
        },
    ) { padding ->
        if (groups.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("미분석 사진이 없습니다", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            return@Scaffold
        }

        LazyColumn(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            groups.forEachIndexed { index, group ->
                item(key = "group-$index-${group.first().id}") {
                    GroupCard(
                        group = group,
                        selected = selected,
                        onToggle = { photo ->
                            selected = if (photo.id in selected) selected - photo.id
                            else selected + photo.id
                        },
                        onSelectGroup = {
                            selected = selected + group.map { it.id }
                        },
                        onCreateMeal = {
                            val photos = group.filter { it.id in selected }
                                .ifEmpty { group }
                            scope.launch {
                                val mealId = vm.createMeal(photos)
                                selected = selected - photos.map { it.id }.toSet()
                                nav.navigate(Routes.meal(mealId))
                            }
                        },
                        onDelete = { vm.deletePhoto(it) },
                    )
                }
            }
            item { Spacer(Modifier.height(32.dp)) }
        }
    }
}

@Composable
private fun GroupCard(
    group: List<Photo>,
    selected: Set<String>,
    onToggle: (Photo) -> Unit,
    onSelectGroup: () -> Unit,
    onCreateMeal: () -> Unit,
    onDelete: (Photo) -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    group.first().takenAt.toDateTimeString(),
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = onSelectGroup) { Text("전체 선택") }
            }
            LazyVerticalGrid(
                columns = GridCells.Adaptive(80.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(((group.size / 4 + 1) * 88).coerceAtMost(280).dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                userScrollEnabled = false,
            ) {
                items(group, key = { it.id }) { photo ->
                    Box(Modifier.clickable { onToggle(photo) }) {
                        PhotoThumb(photo, size = 80.dp)
                        if (photo.id in selected) {
                            Icon(
                                Icons.Default.CheckCircle,
                                contentDescription = "선택됨",
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier
                                    .align(Alignment.TopEnd)
                                    .padding(4.dp)
                                    .size(20.dp),
                            )
                        }
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Button(onClick = onCreateMeal, modifier = Modifier.fillMaxWidth()) {
                val count = group.count { it.id in selected }
                Text(if (count > 0) "선택한 ${count}장으로 끼니 만들기" else "이 사진들로 끼니 만들기")
            }
        }
    }
}
