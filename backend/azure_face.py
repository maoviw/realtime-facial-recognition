import os
import logging
from typing import List, Dict, Any

from azure.cognitiveservices.vision.face import FaceClient
from msrest.authentication import CognitiveServicesCredentials
from azure.cognitiveservices.vision.face.models import FaceAttributeType, APIErrorException

# Configuration du logging
logger = logging.getLogger("recognition.azure")


class AzureFaceService:
    """Service indépendant pour interagir avec l'API Azure Cognitive Services Face."""
    
    def __init__(self, endpoint: str, key: str) -> None:
        """Initialise le client avec l'endpoint et la clé Azure."""
        if not endpoint or not key:
            raise ValueError(
                "Les variables AZURE_FACE_ENDPOINT et AZURE_FACE_KEY "
                "doivent être définies."
            )
            
        # Initialisation du client officiel Azure
        credentials = CognitiveServicesCredentials(key)
        self.client = FaceClient(endpoint, credentials)
        logger.info("FaceClient Azure initialisé avec succès.")

    def detect_face_basic(self, image_data: bytes) -> List[Dict[str, Any]]:
        """
        Détecte les visages dans un flux d'octets.
        Récupère : la Bounding Box, le port de lunettes, et la présence de masque.
        """
        # Sélection stricte des attributs autorisés publiquement et supportés par detection_03
        features = [
            FaceAttributeType.glasses,
            FaceAttributeType.mask
        ]

        import io
        stream = io.BytesIO(image_data)

        try:
            detected_faces = self.client.face.detect_with_stream(
                image=stream,
                return_face_id=False,          # Non requis pour une détection basique
                return_face_landmarks=False,   # Non requis
                return_face_attributes=features,
                detection_model="detection_03",# Modèle recommandé pour la détection
                recognition_model="recognition_04" # Recommandé en duo avec detection_03
            )
            
            results = []
            if not detected_faces:
                return results

            # Formatage de la réponse pour l'isoler du SDK Azure
            for face in detected_faces:
                attrs = face.face_attributes
                
                # Parsing sécurisé du masque
                mask_info = None
                if attrs and attrs.mask:
                    mask_info = {
                        "type": attrs.mask.type.name if attrs.mask.type else "noMask",
                        "nose_and_mouth_covered": attrs.mask.nose_and_mouth_covered
                    }

                # Parsing des accessoires
                accessories_info = []
                if attrs and attrs.accessories:
                    accessories_info = [
                        {"type": acc.type.name, "confidence": round(acc.confidence, 2)}
                        for acc in attrs.accessories
                    ]

                face_data = {
                    "bounding_box": {
                        "top": face.face_rectangle.top,
                        "left": face.face_rectangle.left,
                        "width": face.face_rectangle.width,
                        "height": face.face_rectangle.height,
                    },
                    "glasses": attrs.glasses.name if (attrs and attrs.glasses) else "NoGlasses",
                    "mask": mask_info,
                    "accessories": accessories_info
                }
                results.append(face_data)
                
            return results

        except APIErrorException as api_err:
            logger.error(f"Erreur API Azure: {api_err}")
            raise
        except Exception as e:
            logger.error(f"Une erreur inattendue est survenue: {e}")
            raise

    def analyze_face_quality(self, image_source: str | bytes) -> List[Dict[str, Any]]:
        """
        Analyse la qualité des visages dans une image (chemin local ou octets).
        Demande spécifiquement : le flou, l'exposition, l'occlusion, la posture de la tête, ainsi que les masques et lunettes.
        Renvoie un score global de qualité et les détails.
        """
        features = [
            FaceAttributeType.blur,
            FaceAttributeType.exposure,
            FaceAttributeType.occlusion,
            FaceAttributeType.head_pose,
            FaceAttributeType.glasses,
            FaceAttributeType.mask
        ]
        
        import io
        if isinstance(image_source, str):
            if not os.path.isfile(image_source):
                raise FileNotFoundError(f"L'image est introuvable au chemin: {image_source}")
            with open(image_source, "rb") as f:
                stream = io.BytesIO(f.read())
        else:
            stream = io.BytesIO(image_source)

        try:
            detected_faces = self.client.face.detect_with_stream(
                image=stream,
                return_face_id=False,
                return_face_landmarks=False,
                return_face_attributes=features,
                detection_model="detection_03",
                recognition_model="recognition_04"
            )
            
            results = []
            if not detected_faces:
                return results

            for face in detected_faces:
                attrs = face.face_attributes
                
                # --- Évaluation de la qualité (Logique métier) ---
                is_good_quality = True
                issues = []
                
                blur_data = None
                if attrs and attrs.blur:
                    blur_data = {"level": attrs.blur.blur_level.name, "value": round(attrs.blur.value, 2)}
                    if attrs.blur.blur_level.name == "high":
                        is_good_quality = False
                        issues.append("Image trop floue")
                        
                exposure_data = None
                if attrs and attrs.exposure:
                    level_name = attrs.exposure.exposure_level.name if hasattr(attrs.exposure.exposure_level, "name") else str(attrs.exposure.exposure_level)
                    exposure_data = {"level": level_name, "value": round(attrs.exposure.value, 2)}
                    if "good" not in level_name.lower():
                        is_good_quality = False
                        issues.append(f"Mauvaise exposition: {level_name}")
                        
                occlusion_data = None
                if attrs and attrs.occlusion:
                    occlusion_data = {
                        "forehead_occluded": attrs.occlusion.forehead_occluded,
                        "eye_occluded": attrs.occlusion.eye_occluded,
                        "mouth_occluded": attrs.occlusion.mouth_occluded
                    }
                    if attrs.occlusion.eye_occluded or attrs.occlusion.mouth_occluded:
                        is_good_quality = False
                        issues.append("Parties importantes du visage occultées (yeux ou bouche)")
                        
                head_pose_data = None
                if attrs and attrs.head_pose:
                    pitch = round(attrs.head_pose.pitch, 1)
                    yaw = round(attrs.head_pose.yaw, 1)
                    roll = round(attrs.head_pose.roll, 1)
                    head_pose_data = {"pitch": pitch, "yaw": yaw, "roll": roll}
                    
                    if abs(pitch) > 15 or abs(yaw) > 15:
                        is_good_quality = False
                        issues.append("Tête trop inclinée ou tournée")

                mask_info = None
                if attrs and attrs.mask:
                    mask_info = {
                        "type": attrs.mask.type.name if attrs.mask.type else "noMask",
                        "nose_and_mouth_covered": attrs.mask.nose_and_mouth_covered
                    }

                face_data = {
                    "bounding_box": {
                        "top": face.face_rectangle.top,
                        "left": face.face_rectangle.left,
                        "width": face.face_rectangle.width,
                        "height": face.face_rectangle.height,
                    },
                    "glasses": attrs.glasses.name if (attrs and attrs.glasses) else "NoGlasses",
                    "mask": mask_info,
                    "quality_analysis": {
                        "is_sufficient_quality": is_good_quality,
                        "issues": issues,
                        "metrics": {
                            "blur": blur_data,
                            "exposure": exposure_data,
                            "occlusion": occlusion_data,
                            "head_pose": head_pose_data
                        }
                    }
                }
                results.append(face_data)
                
            return results

        except APIErrorException as api_err:
            logger.error(f"Erreur API Azure: {api_err}")
            raise
        except Exception as e:
            logger.error(f"Une erreur inattendue est survenue: {e}")
            raise

    def verify_two_faces(self, image_path_1: str | bytes, image_path_2: str | bytes) -> Dict[str, Any]:
        """
        Vérifie si deux visages appartiennent à la même personne en utilisant l'API Azure Face.
        
        1. Détecte le visage 1 -> récupère le face_id
        2. Détecte le visage 2 -> récupère le face_id
        3. Compare les deux via verify_face_to_face
        
        Gère les cas où aucun visage n'est trouvé de manière sécurisée.
        """
        import io
        
        def _get_stream(src: str | bytes):
            if isinstance(src, str):
                if not os.path.isfile(src):
                    raise FileNotFoundError(f"Image introuvable : {src}")
                with open(src, "rb") as f:
                    return io.BytesIO(f.read())
            return io.BytesIO(src)

        try:
            # 1. Détection visage 1 avec detection_03 et récupération du face_id
            faces_1 = self.client.face.detect_with_stream(
                image=_get_stream(image_path_1),
                return_face_id=True,  # Crucial pour obtenir le face_id (valable 24h)
                detection_model="detection_03",
                recognition_model="recognition_04"
            )
            
            if not faces_1:
                return {
                    "is_identical": False, 
                    "confidence": 0.0, 
                    "error": "Aucun visage n'a été détecté sur la première image."
                }
            
            face_id_1 = faces_1[0].face_id

            # 2. Détection visage 2 avec detection_03 et récupération du face_id
            faces_2 = self.client.face.detect_with_stream(
                image=_get_stream(image_path_2),
                return_face_id=True,
                detection_model="detection_03",
                recognition_model="recognition_04"
            )
            
            if not faces_2:
                return {
                    "is_identical": False, 
                    "confidence": 0.0, 
                    "error": "Aucun visage n'a été détecté sur la seconde image."
                }

            face_id_2 = faces_2[0].face_id

            # 3. Comparaison des deux face_id
            verify_result = self.client.face.verify_face_to_face(
                face_id1=face_id_1,
                face_id2=face_id_2
            )

            # 4. Traitement et formatage du résultat
            return {
                "is_identical": verify_result.is_identical,
                "confidence": round(verify_result.confidence, 4),
                "error": None
            }

        except APIErrorException as api_err:
            error_msg = f"Erreur de l'API Azure Face: {api_err}"
            logger.error(error_msg)
            return {"is_identical": False, "confidence": 0.0, "error": error_msg}
            
        except Exception as e:
            error_msg = f"Erreur inattendue: {e}"
            logger.error(error_msg)
            return {"is_identical": False, "confidence": 0.0, "error": error_msg}

    def create_or_get_group(self, group_id: str, name: str) -> None:
        """
        Crée un LargePersonGroup s'il n'existe pas.
        """
        try:
            self.client.large_person_group.get(large_person_group_id=group_id)
            logger.info(f"Le LargePersonGroup '{group_id}' existe déjà.")
        except APIErrorException as e:
            # Vérifier si l'erreur vient du fait que le groupe n'existe pas
            if hasattr(e, 'error') and hasattr(e.error, 'code') and e.error.code == 'LargePersonGroupNotFound':
                self.client.large_person_group.create(
                    large_person_group_id=group_id,
                    name=name,
                    recognition_model="recognition_04"
                )
                logger.info(f"LargePersonGroup '{group_id}' créé avec succès.")
            elif 'LargePersonGroupNotFound' in str(e):
                self.client.large_person_group.create(
                    large_person_group_id=group_id,
                    name=name,
                    recognition_model="recognition_04"
                )
                logger.info(f"LargePersonGroup '{group_id}' créé avec succès.")
            else:
                logger.error(f"Erreur lors de la vérification du groupe : {e}")
                raise

    def add_person_to_group(self, group_id: str, person_name: str, images_list: List[str]) -> str:
        """
        Ajoute une personne au groupe, lui associe ses photos de référence, puis déclenche l'entraînement du groupe.
        Retourne le person_id de la personne créée.
        """
        import io
        try:
            # 1. Créer la personne dans le groupe
            person = self.client.large_person_group_person.create(
                large_person_group_id=group_id,
                name=person_name
            )
            person_id = person.person_id
            
            # 2. Ajouter les visages à la personne
            for img_path in images_list:
                if not os.path.isfile(img_path):
                    logger.warning(f"Fichier image ignoré car introuvable : {img_path}")
                    continue
                    
                with open(img_path, "rb") as f:
                    stream = io.BytesIO(f.read())
                    self.client.large_person_group_person.add_face_from_stream(
                        large_person_group_id=group_id,
                        person_id=person_id,
                        image=stream,
                        detection_model="detection_03"
                    )
            
            # 3. Lancer l'entraînement
            self.client.large_person_group.train(large_person_group_id=group_id)
            logger.info(f"Entraînement déclenché pour le groupe '{group_id}' après ajout de {person_name}.")
            
            return str(person_id)

        except APIErrorException as api_err:
            logger.error(f"Erreur Azure lors de l'ajout de personne : {api_err}")
            raise
        except Exception as e:
            logger.error(f"Erreur inattendue : {e}")
            raise

    def identify_face_in_group(self, image_source: str | bytes, group_id: str) -> Dict[str, Any]:
        """
        Détecte le visage dans l'image cible, utilise la méthode d'identification globale d'Azure 
        sur le groupe spécifié, et renvoie le nom de la personne détectée si le score > 0.65.
        Gère l'attente de l'entraînement si nécessaire.
        """
        import io
        import time

        def _get_stream(src: str | bytes):
            if isinstance(src, str):
                if not os.path.isfile(src):
                    raise FileNotFoundError(f"Image introuvable : {src}")
                with open(src, "rb") as f:
                    return io.BytesIO(f.read())
            return io.BytesIO(src)

        try:
            # 1. Vérifier le statut de l'entraînement
            while True:
                training_status = self.client.large_person_group.get_training_status(large_person_group_id=group_id)
                status_str = str(training_status.status).lower()
                if "succeeded" in status_str:
                    break
                elif "failed" in status_str:
                    return {"name": None, "confidence": 0.0, "error": "L'entraînement du groupe a échoué."}
                
                logger.info("En attente de l'entraînement du groupe Azure...")
                time.sleep(1)

            # 2. Détecter le visage pour obtenir un face_id
            faces = self.client.face.detect_with_stream(
                image=_get_stream(image_source),
                return_face_id=True,
                detection_model="detection_03",
                recognition_model="recognition_04"
            )

            if not faces:
                return {"name": None, "confidence": 0.0, "error": "Aucun visage détecté sur l'image."}

            face_id = faces[0].face_id

            # 3. Identifier le visage dans le LargePersonGroup
            identify_results = self.client.face.identify(
                face_ids=[face_id],
                large_person_group_id=group_id,
                max_num_of_candidates_returned=1,
                confidence_threshold=0.65
            )

            if not identify_results or not identify_results[0].candidates:
                return {"name": None, "confidence": 0.0, "error": "Visage non reconnu dans le groupe."}

            candidate = identify_results[0].candidates[0]
            
            # 4. Récupérer le nom de la personne via son person_id
            person = self.client.large_person_group_person.get(
                large_person_group_id=group_id,
                person_id=candidate.person_id
            )

            return {
                "name": person.name,
                "confidence": round(candidate.confidence, 4),
                "error": None
            }

        except APIErrorException as api_err:
            error_msg = f"Erreur API Azure lors de l'identification : {api_err}"
            logger.error(error_msg)
            return {"name": None, "confidence": 0.0, "error": error_msg}
        except Exception as e:
            error_msg = f"Erreur inattendue : {e}"
            logger.error(error_msg)
            return {"name": None, "confidence": 0.0, "error": error_msg}
