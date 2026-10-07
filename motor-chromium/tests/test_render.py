from clearcote._render import evaluate_render_info


def test_coherent_nvidia_persona():
    v = evaluate_render_info({
        "webgl": True, "webgl2": True,
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "maxTextureSize": 16384,
    })
    assert v["coherent"] is True
    assert v["software_suspected"] is False
    assert v["warnings"] == []


def test_software_rasterizer_is_a_fatal_tell():
    v = evaluate_render_info({
        "webgl": True,
        "vendor": "Google Inc. (Google)",
        "renderer": "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (LLVM 16.0.0)), SwiftShader driver)",
    })
    assert v["software_suspected"] is True
    assert v["coherent"] is False
    assert any("software rasterizer" in w for w in v["warnings"])


def test_incoherent_vendor_renderer_family():
    v = evaluate_render_info({
        "webgl": True,
        "vendor": "Google Inc. (Apple)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3080, D3D11)",
    })
    assert v["coherent"] is False
    assert any("disagree on GPU family" in w for w in v["warnings"])


def test_no_webgl_is_a_tell():
    v = evaluate_render_info({"webgl": False})
    assert v["coherent"] is False
    assert any("WebGL is unavailable" in w for w in v["warnings"])


def test_claimed_gpu_mismatch():
    v = evaluate_render_info(
        {"webgl": True, "vendor": "Google Inc. (Intel)", "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770, D3D11)"},
        claimed_gpu="NVIDIA GeForce RTX 4090",
    )
    assert v["coherent"] is False
    assert any("does not match" in w for w in v["warnings"])


def test_unmasked_pair_preferred_over_masked():
    v = evaluate_render_info({
        "webgl": True,
        "vendor": "WebKit", "renderer": "WebKit WebGL",
        "unmaskedVendor": "Google Inc. (Intel)",
        "unmaskedRenderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics, D3D11)",
    })
    assert v["renderer"].startswith("ANGLE (Intel")
    assert v["coherent"] is True


# -- capability floor: the string check is defeated once the persona renames the backend ---------
# Values below are measured on a GPU-less Linux VPS with clearcote 153 r25 and genuine Chrome 153.

def test_spoofed_renderer_over_swiftshader_is_caught_by_the_capability_floor():
    """Headless Linux: the persona renames SwiftShader to an Intel UHD 770, so _SOFTWARE_MARKERS
    sees nothing -- but MAX_TEXTURE_SIZE is still SwiftShader's 8192, not a desktop GPU's 16384."""
    v = evaluate_render_info({
        "webgl": True, "webgl2": True,
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Mesa Intel(R) UHD Graphics 770 (RPL-S), OpenGL 4.6 (Core Profile) "
                    "Mesa 23.2.1-1ubuntu3.1~22.04.2)",
        "maxTextureSize": 8192,
        "maxRenderbufferSize": 8192,
        "maxVertexUniformVectors": 4096,
        "maxFragmentUniformVectors": 4096,
        "canAllocate16k": False,
    })
    assert v["software_suspected"] is True
    assert v["coherent"] is False
    assert any("MAX_TEXTURE_SIZE is 8192" in w for w in v["warnings"])


def test_headed_linux_llvmpipe_backed_persona_stays_coherent():
    """Same persona headed, where --ignore-gpu-blocklist reaches Mesa llvmpipe: 16384 across the
    board and vertex == fragment, so nothing should fire."""
    v = evaluate_render_info({
        "webgl": True, "webgl2": True,
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Mesa Intel(R) UHD Graphics 770 (RPL-S), OpenGL 4.6 (Core Profile) "
                    "Mesa 23.2.1-1ubuntu3.1~22.04.2)",
        "maxTextureSize": 16384,
        "maxRenderbufferSize": 16384,
        "maxVertexUniformVectors": 1024,
        "maxFragmentUniformVectors": 1024,
        "canAllocate16k": True,
    })
    assert v["software_suspected"] is False
    assert v["coherent"] is True
    assert v["warnings"] == []


def test_angle_d3d11_vertex_fragment_split_is_not_a_tell():
    """Genuine Chrome on an RTX 3070 reports 4095/1024 -- the uniform-vector check must not fire
    on a D3D11 renderer, or every real Windows machine trips it."""
    v = evaluate_render_info({
        "webgl": True, "webgl2": True,
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770 (0xA780) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "maxTextureSize": 16384,
        "maxVertexUniformVectors": 4095,
        "maxFragmentUniformVectors": 1024,
        "canAllocate16k": True,
    })
    assert v["coherent"] is True
    assert v["warnings"] == []


def test_half_applied_persona_splits_uniform_vectors_under_a_gl_renderer():
    """clearcote headless Linux: MAX_FRAGMENT_UNIFORM_VECTORS takes the persona's 1024 while
    MAX_VERTEX_UNIFORM_VECTORS passes SwiftShader's 4096 through. No real GL driver does that."""
    v = evaluate_render_info({
        "webgl": True, "webgl2": True,
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Mesa Intel(R) UHD Graphics 770 (RPL-S), OpenGL 4.6 (Core Profile) "
                    "Mesa 23.2.1-1ubuntu3.1~22.04.2)",
        "maxTextureSize": 16384,
        "maxVertexUniformVectors": 4096,
        "maxFragmentUniformVectors": 1024,
        "canAllocate16k": True,
    })
    assert v["coherent"] is False
    assert any("MAX_VERTEX_UNIFORM_VECTORS" in w for w in v["warnings"])


def test_capability_floor_skips_mobile_gpu_families():
    """A Mali/Adreno persona legitimately reports 8192; the floor is a desktop-claim check only."""
    v = evaluate_render_info({
        "webgl": True, "webgl2": True,
        "vendor": "ARM", "renderer": "Mali-G78",
        "maxTextureSize": 8192,
        "maxVertexUniformVectors": 1024, "maxFragmentUniformVectors": 1024,
    })
    assert v["software_suspected"] is False
    assert v["coherent"] is True


def test_capability_floor_is_inert_when_the_probe_did_not_report_limits():
    """Old callers passing only vendor/renderer must keep their previous verdict."""
    v = evaluate_render_info({
        "webgl": True,
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics, D3D11)",
    })
    assert v["coherent"] is True
    assert v["warnings"] == []
