# Compositing variants (mask set name, blend2.compose options). Shared by blend_exp.py / gfp_exp.py.
VARIANTS = {
    "v0_baseline": ("m90_90", {}),
    "v1_lab": ("m90_90", {"lab": True}),
    "v2_lf": ("m90_90", {"lf": True}),
    "v3_lf_detail": ("m90_90", {"lf": True, "detail": 1.0, "sharpen": 0.6, "grain": True}),
    "v4_seam": ("m90_90_u57", {"lf": True, "detail": 1.0, "sharpen": 0.6, "grain": True, "erode": 6, "feather": 10}),
    "v5_seam_narrow": ("m90_40_u57", {"lf": True, "detail": 1.0, "sharpen": 0.6, "grain": True, "erode": 6, "feather": 10}),
    "v6_lab_lf_detail_seam": ("m90_90_u57", {"lab": True, "lf": True, "detail": 1.0, "sharpen": 0.6, "grain": True, "erode": 6, "feather": 10}),
    "v7_lipcolor": ("m90_90_u57", {"lf": True, "lipcolor": 1.0, "detail": 1.0, "sharpen": 1.0, "grain": True, "erode": 6, "feather": 10}),
    "v8_lipcolor70": ("m90_90_u57", {"lf": True, "lipcolor": 0.7, "detail": 1.0, "sharpen": 0.8, "grain": True, "erode": 6, "feather": 10}),
    "v9_lip_gloss_clarity": ("m90_90_u57", {"lf": True, "lipcolor": 1.0, "clarity": 0.8, "gloss": 1.0, "detail": 1.0, "sharpen": 0.8, "grain": True, "erode": 6, "feather": 10}),
    "v10_clarity_only": ("m90_90_u57", {"lf": True, "lipcolor": 1.0, "clarity": 0.8, "detail": 1.0, "sharpen": 0.8, "grain": True, "erode": 6, "feather": 10}),
    "v11_v9_tema60": ("m90_90_u57", {"tema": 0.6, "lf": True, "lipcolor": 1.0, "clarity": 0.8, "gloss": 1.0, "detail": 1.0, "sharpen": 0.8, "grain": True, "erode": 6, "feather": 10}),
    "v12_v7_tema60": ("m90_90_u57", {"tema": 0.6, "lf": True, "lipcolor": 1.0, "detail": 1.0, "sharpen": 1.0, "grain": True, "erode": 6, "feather": 10}),
    "v0t_baseline_tema60": ("m90_90", {"tema": 0.6}),
}
