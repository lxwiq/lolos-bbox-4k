// Pas de console en plus de la fenêtre sous Windows
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    lolos_lib::run()
}
