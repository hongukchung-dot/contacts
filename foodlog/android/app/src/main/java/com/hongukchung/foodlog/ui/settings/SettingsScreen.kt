package com.hongukchung.foodlog.ui.settings

import android.text.format.Formatter
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.navigation.NavHostController
import com.hongukchung.foodlog.BuildConfig
import com.hongukchung.foodlog.data.BackupManager
import com.hongukchung.foodlog.ui.app
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(nav: NavHostController) {
    val application = app()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    val settings by application.settings.settings.collectAsState(initial = null)

    var serverUrl by remember { mutableStateOf<String?>(null) }
    var appToken by remember { mutableStateOf<String?>(null) }
    var goal by remember { mutableStateOf<String?>(null) }
    var keepMonths by remember { mutableStateOf<String?>(null) }
    var storageBytes by remember { mutableStateOf(0L) }
    var restoreUri by remember { mutableStateOf<android.net.Uri?>(null) }

    LaunchedEffect(Unit) { storageBytes = application.photoStore.storageUsageBytes() }

    val exportLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/zip"),
    ) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            val ok = application.backupManager.export(uri, BuildConfig.VERSION_NAME)
            if (ok) {
                application.settings.setLastBackupAt(System.currentTimeMillis())
                snackbar.showSnackbar("백업 파일을 만들었습니다")
            } else {
                snackbar.showSnackbar("백업에 실패했습니다")
            }
        }
    }

    val importLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let { restoreUri = it } }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text("설정") },
                navigationIcon = {
                    IconButton(onClick = { nav.popBackStack() }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "뒤로")
                    }
                },
            )
        },
    ) { padding ->
        val s = settings ?: return@Scaffold
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // ------------------------------------------------ 서버
            Text("서버", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = serverUrl ?: s.serverBaseUrl,
                onValueChange = { serverUrl = it },
                label = { Text("서버 주소 (예: https://host.ts.net/foodlog)") },
                enabled = BuildConfig.SERVER_EDITABLE,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = appToken ?: s.appToken,
                onValueChange = { appToken = it },
                label = { Text("앱 토큰") },
                enabled = BuildConfig.SERVER_EDITABLE,
                modifier = Modifier.fillMaxWidth(),
            )
            if (BuildConfig.SERVER_EDITABLE) {
                OutlinedButton(onClick = {
                    scope.launch {
                        serverUrl?.let { application.settings.setServerBaseUrl(it) }
                        appToken?.let { application.settings.setAppToken(it) }
                        val ok = application.api.health()
                        snackbar.showSnackbar(if (ok) "서버 연결 확인" else "서버에 연결할 수 없습니다")
                    }
                }) { Text("저장 후 연결 확인") }
            } else {
                Text(
                    "서버 설정은 관리자 버전에서만 변경할 수 있습니다",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            HorizontalDivider()

            // ------------------------------------------------ 목표
            Text("목표", style = MaterialTheme.typography.titleSmall)
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                OutlinedTextField(
                    value = goal ?: s.dailyGoalKcal.toString(),
                    onValueChange = { goal = it },
                    label = { Text("일일 목표 열량 (kcal)") },
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                TextButton(onClick = {
                    goal?.toIntOrNull()?.let {
                        scope.launch {
                            application.settings.setDailyGoalKcal(it)
                            snackbar.showSnackbar("저장했습니다")
                        }
                    }
                }) { Text("저장") }
            }

            HorizontalDivider()

            // ------------------------------------------------ 백업/복원 (방식 A)
            Text("백업 / 복원", style = MaterialTheme.typography.titleSmall)
            if (s.lastBackupAt > 0) {
                Text(
                    "마지막 백업: ${java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", java.util.Locale.KOREA).format(java.util.Date(s.lastBackupAt))}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    val name = "foodlog-backup-${LocalDate.now().format(DateTimeFormatter.BASIC_ISO_DATE)}.zip"
                    exportLauncher.launch(name)
                }) { Text("백업 파일 만들기") }
                OutlinedButton(onClick = {
                    importLauncher.launch(arrayOf("application/zip"))
                }) { Text("복원") }
            }
            Text(
                "저장 위치 선택 화면에서 Google Drive 폴더를 고르면 드라이브로 백업됩니다.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            HorizontalDivider()

            // ------------------------------------------------ 저장 공간
            Text("저장 공간", style = MaterialTheme.typography.titleSmall)
            Text("사진 사용량: ${Formatter.formatFileSize(context, storageBytes)}")
            Card {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "⚠️ 사진 원본은 앱 전용 저장소에 있어 앱을 삭제하면 함께 지워집니다. 주기적으로 백업하세요.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = keepMonths ?: s.keepOriginalMonths.toString(),
                            onValueChange = { keepMonths = it },
                            label = { Text("원본 보관 개월 수 (0 = 무기한)") },
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(8.dp))
                        TextButton(onClick = {
                            val months = (keepMonths ?: s.keepOriginalMonths.toString()).toIntOrNull() ?: 0
                            scope.launch {
                                application.settings.setKeepOriginalMonths(months)
                                val deleted = application.mealRepository.cleanupOriginalsOlderThan(months)
                                storageBytes = application.photoStore.storageUsageBytes()
                                snackbar.showSnackbar(
                                    if (months > 0) "원본 ${deleted}장을 정리했습니다 (썸네일 유지)"
                                    else "원본을 무기한 보관합니다",
                                )
                            }
                        }) { Text("정리 실행") }
                    }
                }
            }

            Spacer(Modifier.height(32.dp))
        }
    }

    restoreUri?.let { uri ->
        AlertDialog(
            onDismissRequest = { restoreUri = null },
            title = { Text("복원 방식") },
            text = {
                Text("병합: 같은 id는 건너뛰고 새 데이터만 추가\n전체 교체: 현재 데이터를 모두 지우고 백업으로 대체")
            },
            confirmButton = {
                TextButton(onClick = {
                    restoreUri = null
                    scope.launch {
                        val result = application.backupManager.restore(uri, BackupManager.RestoreMode.MERGE)
                        snackbar.showSnackbar(
                            result?.let { "복원 완료: 끼니 ${it.meals} · 항목 ${it.items} · 사진 ${it.photos}" }
                                ?: "복원에 실패했습니다",
                        )
                    }
                }) { Text("병합") }
            },
            dismissButton = {
                TextButton(onClick = {
                    restoreUri = null
                    scope.launch {
                        val result = application.backupManager.restore(uri, BackupManager.RestoreMode.REPLACE)
                        snackbar.showSnackbar(
                            result?.let { "복원 완료: 끼니 ${it.meals} · 항목 ${it.items} · 사진 ${it.photos}" }
                                ?: "복원에 실패했습니다",
                        )
                    }
                }) { Text("전체 교체") }
            },
        )
    }
}
