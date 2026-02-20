# Пакет обработчиков администратора
from .orders import admin_orders_conv
from .payments import admin_payments_conv
from .reports import admin_reports_conv  # это уже должно быть, но проверьте
from .settings import admin_settings_conv
from .backup import manual_backup
from .restore import restore_conv
from .add_test_seller import add_seller_handler
