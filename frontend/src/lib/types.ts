export interface FaceRectangle {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface FaceData {
  faceRectangle: FaceRectangle;
  recognized: boolean;
  confidence: number;
  name: string | null;
  pitch: number;
  yaw: number;
  roll: number;
  system_action: string;
  glasses?: string;
  mask?: {
    type: string;
    nose_and_mouth_covered: boolean;
  } | null;
  quality?: {
    is_sufficient_quality: boolean;
    issues: string[];
    metrics: {
      blur?: { level: string; value: number };
      exposure?: { level: string; value: number };
      occlusion?: {
        forehead_occluded: boolean;
        eye_occluded: boolean;
        mouth_occluded: boolean;
      };
    };
  };
  demographics?: {
    age: number;
    gender: string;
    emotion: string;
  } | null;
}

export interface ReferenceFace {
  id: number;
  name: string;
  auto?: boolean;
  created_at?: string;
}

export interface RecognitionEvent {
  id: number;
  name: string | null;
  recognized: boolean;
  confidence: number;
  pitch: number | null;
  yaw: number | null;
  roll: number | null;
  system_action: string | null;
  created_at: string;
}

export interface IpCameraConfig {
  enabled: boolean;
  url: string;
  username?: string;
  password?: string;
}

export interface HealthStatus {
  status: string;
  azure_configured: boolean;
  deepface_available: boolean;
  references: number;
  recognition_action: string;
}
