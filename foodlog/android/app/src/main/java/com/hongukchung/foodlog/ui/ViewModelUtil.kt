package com.hongukchung.foodlog.ui

import androidx.compose.runtime.Composable
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.hongukchung.foodlog.FoodLogApp

@Composable
inline fun <reified VM : ViewModel> appViewModel(
    key: String? = null,
    crossinline create: (FoodLogApp) -> VM,
): VM {
    val application = app()
    return viewModel(
        key = key,
        factory = viewModelFactory { initializer { create(application) } },
    )
}
