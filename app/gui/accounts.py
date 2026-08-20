"""Accounts page: connect/disconnect provider accounts via official flows."""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.bus import EventBus
from ..database.accounts import AccountsStore
from ..providers.registry import ProviderRegistry

log = logging.getLogger(__name__)


class _AccountCard(QFrame):
    def __init__(
        self,
        provider_id: str,
        display_name: str,
        store: AccountsStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider_id = provider_id
        self.store = store
        self._build_ui(display_name)
        self.refresh()

    def _build_ui(self, display_name: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        name = QLabel(display_name)
        name.setObjectName("SectionTitle")
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.button = QPushButton()
        self.button.setMinimumWidth(110)
        layout.addWidget(name)
        layout.addWidget(self.status_label, 1)
        layout.addWidget(self.button)
        self.button.clicked.connect(self._on_click)

    def refresh(self) -> None:
        account = self.store.get(self.provider_id)
        if account.connected:
            self.status_label.setText(
                f"Connected{(' as ' + account.display_name) if account.display_name else ''}"
            )
            self.button.setText("Disconnect")
            self.button.setObjectName("DangerButton")
        else:
            self.status_label.setText("Not connected")
            self.button.setText("Connect")
            self.button.setObjectName("")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)

    def _on_click(self) -> None:
        if self.store.is_connected(self.provider_id):
            self.store.disconnect(self.provider_id)
        else:
            self._connect_flow()

    def _connect_flow(self) -> None:
        """Guide the user through the official permission flow."""
        QMessageBox.information(
            self,
            f"Connect {self.provider_id.title()}",
            "This application only uses the official permission flows provided "
            "by the platform. You are responsible for the content you download "
            "and must respect the platform's Terms of Service and copyright.\n\n"
            "Credentials are stored only if your platform provider supplies an "
            "official OAuth token, and are kept in your OS credential vault.",
        )
        # Register a placeholder connection for providers that expose tokens.
        # Real OAuth setup is provider-specific and configured via .env.
        self.store.set_connected(
            self.provider_id,
            display_name=f"{self.provider_id.title()} account",
            scopes=[],
        )
        self.refresh()


class AccountsPage(QWidget):
    changed = Signal()

    def __init__(
        self,
        registry: ProviderRegistry,
        store: AccountsStore,
        bus: EventBus,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.store = store
        self.bus = bus
        self._cards: dict[str, _AccountCard] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        title = QLabel("Accounts")
        title.setObjectName("Title")
        root.addWidget(title)

        info = QLabel(
            "Connect accounts through the official access granted by each "
            "platform. This application never collects passwords, cookies or "
            "session tokens."
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        root.addWidget(info)

        for provider in self.registry.all():
            card = _AccountCard(provider.id, provider.display_name, self.store)
            card.button.clicked.connect(self.changed.emit)
            root.addWidget(card)
            self._cards[provider.id] = card

        root.addStretch(1)

    def refresh(self) -> None:
        for card in self._cards.values():
            card.refresh()