import flet as ft

from ...app_manager import App
from ...utils.logger import logger


def is_mobile_device(page: ft.Page) -> bool:
    return page.width < 768


def setup_responsive_layout(page: ft.Page, app: App) -> None:
    _ = app.language_manager.language.get("sidebar", {})
    
    # Modern Background
    background = ft.Container(
        expand=True,
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_left,
            end=ft.alignment.bottom_right,
            colors=[
                ft.colors.with_opacity(0.1, ft.colors.BLUE_900),
                ft.colors.with_opacity(0.1, ft.colors.PURPLE_900),
            ],
        ),
    )

    if is_mobile_device(page):
        logger.info("mobile device detected, enable mobile layout")
        app.is_mobile = True
        app.left_navigation_menu.width = 0
        app.left_navigation_menu.visible = False
        
        app.bottom_navigation = ft.NavigationBar(
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.HOME, label=_["home"]),
                ft.NavigationBarDestination(icon=ft.Icons.DASHBOARD_ROUNDED, label=_["recordings"]),
                ft.NavigationBarDestination(icon=ft.Icons.SETTINGS, label=_["settings"]),
                ft.NavigationBarDestination(icon=ft.Icons.DRIVE_FILE_MOVE, label=_["storage"]),
                ft.NavigationBarDestination(icon=ft.Icons.INFO, label=_["about"]),
            ],
            on_change=lambda e: page.go(
                f"/{['home', 'recordings', 'settings', 'storage', 'about'][e.control.selected_index]}"),
        )
        
        app.content_area.expand = True
        
        app.complete_page = ft.Stack(
            controls=[
                background,
                ft.Column(
                    expand=True,
                    spacing=0,
                    controls=[
                        app.content_area,
                        app.bottom_navigation,
                        app.dialog_area,
                        app.snack_bar_area
                    ]
                )
            ],
            expand=True,
        )
    else:
        logger.info("desktop device detected, enable desktop layout")
        app.is_mobile = False
        
        # Glass Sidebar
        from ..theme.modern_theme import GlassContainer
        
        sidebar_container = GlassContainer(
            content=app.left_navigation_menu,
            width=200,
            padding=10,
            margin=ft.margin.only(left=10, top=10, bottom=10),
        )
        
        # Main Content Area with Glass Effect
        content_container = GlassContainer(
            content=app.content_area,
            expand=True,
            padding=20,
            margin=ft.margin.all(10),
        )

        app.complete_page = ft.Stack(
            controls=[
                background,
                ft.Row(
                    expand=True,
                    controls=[
                        sidebar_container,
                        content_container,
                        app.dialog_area,
                        app.snack_bar_area,
                    ]
                )
            ],
            expand=True,
        )