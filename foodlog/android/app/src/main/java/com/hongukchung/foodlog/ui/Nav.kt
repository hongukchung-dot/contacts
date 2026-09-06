package com.hongukchung.foodlog.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.hongukchung.foodlog.ui.barcode.BarcodeScreen
import com.hongukchung.foodlog.ui.capture.CaptureScreen
import com.hongukchung.foodlog.ui.day.DayScreen
import com.hongukchung.foodlog.ui.home.HomeScreen
import com.hongukchung.foodlog.ui.inbox.InboxScreen
import com.hongukchung.foodlog.ui.meal.MealEditScreen
import com.hongukchung.foodlog.ui.settings.SettingsScreen
import com.hongukchung.foodlog.ui.stats.StatsScreen

object Routes {
    const val HOME = "home"
    const val CAPTURE = "capture"
    const val INBOX = "inbox"
    const val STATS = "stats"
    const val SETTINGS = "settings"
    const val MEAL = "meal/{mealId}"
    const val BARCODE = "barcode/{mealId}"
    const val DAY = "day/{day}"

    fun meal(mealId: String) = "meal/$mealId"
    fun barcode(mealId: String) = "barcode/$mealId"
    fun day(day: String) = "day/$day"
}

@Composable
fun FoodLogNavHost(navController: NavHostController = rememberNavController()) {
    NavHost(navController = navController, startDestination = Routes.HOME) {
        composable(Routes.HOME) { HomeScreen(navController) }
        composable(Routes.CAPTURE) { CaptureScreen(navController) }
        composable(Routes.INBOX) { InboxScreen(navController) }
        composable(Routes.STATS) { StatsScreen(navController) }
        composable(Routes.SETTINGS) { SettingsScreen(navController) }
        composable(
            Routes.MEAL,
            arguments = listOf(navArgument("mealId") { type = NavType.StringType }),
        ) { entry ->
            MealEditScreen(navController, entry.arguments?.getString("mealId").orEmpty())
        }
        composable(
            Routes.BARCODE,
            arguments = listOf(navArgument("mealId") { type = NavType.StringType }),
        ) { entry ->
            BarcodeScreen(navController, entry.arguments?.getString("mealId").orEmpty())
        }
        composable(
            Routes.DAY,
            arguments = listOf(navArgument("day") { type = NavType.StringType }),
        ) { entry ->
            DayScreen(navController, entry.arguments?.getString("day").orEmpty())
        }
    }
}
