# Пакет обработчиков администратора
from .orders import admin_orders_conv
from .payments import admin_payments_conv
from .reports import admin_reports_conv
from .settings import admin_settings_conv
from .sellers import admin_sellers_handler
from .backup import manual_backup
from .restore import restore_conv
from .add_test_seller import add_seller_handler  # ЭТО НУЖНО ДОБАВИТЬ
