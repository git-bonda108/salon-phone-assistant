# 0008 · Voice profiles: self-hosted speaker embeddings, per-profile keys

**Context.** Challenge B (Suisse Meets) needs speaker identification. Azure AI Speaker Recognition was retired on 30 September 2025 and has no direct replacement. Voice profiles are biometric data under the revDSG and GDPR.

**Decision.** Use an open, pretrained speaker-embedding model (ECAPA-TDNN class) running in Swiss-hosted containers. Diarisation comes from Suisse Meets itself (the API returns speaker-separated segments with timestamps); the profile service only names the speakers. Each profile is encrypted with its own data key, wrapped by a per-customer key in a key vault. Deleting a profile destroys its key (crypto-shredding).

**Consequences.** We own calibration and measurement (thresholds per device class, false-accept rate). In return, biometric data never leaves infrastructure we control. Details: [voice profiles concept](../voice-profiles/concept.md).
