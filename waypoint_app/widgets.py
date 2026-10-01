"""Inputs that let the surrounding inspector scroll until explicitly clicked."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox


class _ClickWheel:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._wheel_armed = False
        if isinstance(self, QDoubleSpinBox):
            self.lineEdit().installEventFilter(self)

    def mousePressEvent(self, event):
        self._wheel_armed = True
        super().mousePressEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            self._wheel_armed = True
        return super().eventFilter(watched, event)

    def focusOutEvent(self, event):
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self._wheel_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        if self._wheel_armed and self.hasFocus():
            super().wheelEvent(event)
        else:
            # Ignoring the event lets Qt deliver it to the enclosing scroll area.
            event.ignore()


class ClickWheelDoubleSpinBox(_ClickWheel, QDoubleSpinBox):
    pass


class ClickWheelComboBox(_ClickWheel, QComboBox):
    pass
