package com.hongukchung.foodlog

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.hongukchung.foodlog.ui.FoodLogNavHost
import com.hongukchung.foodlog.ui.theme.FoodLogTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            FoodLogTheme {
                FoodLogNavHost()
            }
        }
    }
}
