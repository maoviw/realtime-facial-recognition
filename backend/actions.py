"""Système d'action configurable et cross-platform déclenché à la reconnaissance.

Remplace l'ancien `subprocess.Popen(["notepad.exe"])` (Windows uniquement).
Modes (via RECOGNITION_ACTION) :
  - none    : aucune action
  - log     : journalise simplement l'événement (par défaut)
  - command : exécute une commande shell configurable (RECOGNITION_COMMAND)
  - webhook : appelle une URL HTTP POST (RECOGNITION_WEBHOOK_URL)
Dans tous les cas, l'événement est journalisé.
"""
import logging
import shlex
import subprocess

import requests

from config import mask_name, settings

logger = logging.getLogger("recognition.actions")


def trigger_action(name: str | None, confidence: float) -> str:
    """Déclenche l'action configurée. Retourne un libellé décrivant ce qui a été fait."""
    label = name or "Inconnu"
    # Le nom est masqué dans les logs (PII) ; la valeur en clair reste utilisée
    # pour les actions (commande/webhook) déclenchées localement.
    logger.info("Visage reconnu: %s (confiance=%.2f)", mask_name(label), confidence)

    action = settings.RECOGNITION_ACTION

    if action == "none":
        return "Aucune action"

    if action == "log":
        return "Journalisé"

    if action == "command":
        if not settings.ALLOW_COMMAND_ACTION:
            logger.warning(
                "RECOGNITION_ACTION=command est désactivé "
                "(définir ALLOW_COMMAND_ACTION=true pour l'activer)."
            )
            return "Action commande désactivée"
        cmd = settings.RECOGNITION_COMMAND.strip()
        if not cmd:
            logger.warning("RECOGNITION_ACTION=command mais RECOGNITION_COMMAND est vide.")
            return "Commande non configurée"
        try:
            # Variables disponibles dans la commande
            formatted = cmd.format(name=label, confidence=f"{confidence:.2f}")
            subprocess.Popen(shlex.split(formatted))
            return f"Commande exécutée"
        except Exception as exc:  # noqa: BLE001
            logger.error("Échec de l'exécution de la commande: %s", exc)
            return "Échec commande"

    if action == "webhook":
        url = settings.RECOGNITION_WEBHOOK_URL.strip()
        if not url:
            logger.warning("RECOGNITION_ACTION=webhook mais RECOGNITION_WEBHOOK_URL est vide.")
            return "Webhook non configuré"
        try:
            requests.post(
                url,
                json={"name": label, "confidence": round(confidence, 2)},
                timeout=settings.AZURE_TIMEOUT,
            )
            return "Webhook appelé"
        except requests.RequestException as exc:
            logger.error("Échec de l'appel webhook: %s", exc)
            return "Échec webhook"

    logger.warning("RECOGNITION_ACTION inconnu: %s", action)
    return "Action inconnue"
