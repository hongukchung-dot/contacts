package com.hongukchung.foodlog.ui.capture

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Camera
import androidx.compose.material.icons.filled.Done
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material3.Badge
import androidx.compose.material3.Button
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.navigation.NavHostController
import com.hongukchung.foodlog.ui.app
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.launch

/**
 * 빠른 촬영 (스펙 4.2)
 * - 찍으면 즉시 저장, 아무 것도 묻지 않고 계속 찍을 수 있다 (코스 요리 대응)
 * - 갤러리 가져오기(사진 선택기) 지원 — takenAt은 EXIF에서
 */
@Composable
fun CaptureScreen(nav: NavHostController) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val application = app()
    val scope = rememberCoroutineScope()

    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> hasPermission = granted }

    var capturedCount by remember { mutableIntStateOf(0) }
    val imageCapture = remember {
        ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
            .build()
    }

    val pickerLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(20),
    ) { uris ->
        if (uris.isEmpty()) return@rememberLauncherForActivityResult
        scope.launch {
            var imported = 0
            for (uri in uris) {
                val photo = application.photoStore.importFromUri(uri)
                if (photo != null) {
                    application.database.photoDao().insert(photo)
                    imported++
                }
            }
            capturedCount += imported
        }
    }

    Box(Modifier.fillMaxSize()) {
        if (hasPermission) {
            AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { ctx ->
                    PreviewView(ctx).also { previewView ->
                        scope.launch {
                            val provider = ProcessCameraProvider.getInstance(ctx).await()
                            val preview = androidx.camera.core.Preview.Builder().build().apply {
                                setSurfaceProvider(previewView.surfaceProvider)
                            }
                            provider.unbindAll()
                            provider.bindToLifecycle(
                                lifecycleOwner,
                                CameraSelector.DEFAULT_BACK_CAMERA,
                                preview,
                                imageCapture,
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
                Text("사진을 찍으려면 카메라 권한이 필요합니다")
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

        if (capturedCount > 0) {
            Badge(
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(16.dp),
            ) { Text("저장됨 $capturedCount") }
        }

        Row(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(bottom = 32.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = {
                pickerLauncher.launch(
                    PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                )
            }) {
                Icon(
                    Icons.Default.PhotoLibrary,
                    contentDescription = "갤러리에서 가져오기",
                    tint = Color.White,
                )
            }

            FloatingActionButton(
                onClick = {
                    if (!hasPermission) return@FloatingActionButton
                    val (id, file) = application.photoStore.newCaptureTarget()
                    val output = ImageCapture.OutputFileOptions.Builder(file).build()
                    imageCapture.takePicture(
                        output,
                        ContextCompat.getMainExecutor(context),
                        object : ImageCapture.OnImageSavedCallback {
                            override fun onImageSaved(result: ImageCapture.OutputFileResults) {
                                scope.launch {
                                    val photo = application.photoStore.registerCaptured(id, file)
                                    application.database.photoDao().insert(photo)
                                    capturedCount++
                                }
                            }

                            override fun onError(exception: ImageCaptureException) {
                                file.delete()
                            }
                        },
                    )
                },
                containerColor = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(72.dp),
            ) {
                Icon(Icons.Default.Camera, contentDescription = "촬영")
            }

            IconButton(onClick = { nav.popBackStack() }) {
                Icon(Icons.Default.Done, contentDescription = "완료", tint = Color.White)
            }
        }
    }
}
