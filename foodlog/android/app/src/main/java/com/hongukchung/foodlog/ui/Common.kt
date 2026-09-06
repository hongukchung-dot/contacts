package com.hongukchung.foodlog.ui

import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.hongukchung.foodlog.FoodLogApp
import com.hongukchung.foodlog.data.db.Photo

/** Compose 어디서든 DI 컨테이너 접근 */
@Composable
fun app(): FoodLogApp = LocalContext.current.applicationContext as FoodLogApp

@Composable
fun PhotoThumb(photo: Photo, size: Dp = 72.dp, modifier: Modifier = Modifier) {
    val store = app().photoStore
    AsyncImage(
        model = store.resolveThumb(photo),
        contentDescription = null,
        contentScale = ContentScale.Crop,
        modifier = modifier
            .size(size)
            .clip(RoundedCornerShape(8.dp)),
    )
}
