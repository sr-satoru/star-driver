"""Live render-backend coherence probe (#7).

The launch-time heuristic in :mod:`clearcote._warnings` flags the *config* most likely to leak a
software rasterizer (headless + no canvas bridge). This module is the *page-level* counterpart: it
reads what the page actually sees from WebGL and checks it for the two render-coherence tells a strict
detector looks for:

  1. a **software rasterizer** in the (unmasked) renderer string - SwiftShader / llvmpipe / Mesa
     OffScreen / "Microsoft Basic Render". On a "stealth" browser this is fatal: it means the GPU
     spoof did not apply, or the build is rendering on the CPU. The fix is the canvas bridge (forward
     paints to a real GPU) or running headed on a machine with a real GPU.
  2. an **incoherent vendor/renderer pair** - e.g. a renderer that names an NVIDIA GPU paired with an
     Intel/Apple vendor. A coherent persona never disagrees with itself.

It does NOT (and cannot, from inside the page) read the *real* host GPU when the persona spoofs the
unmasked strings - that is the whole point of the spoof. What it verifies is that the values the page
is allowed to see are internally coherent and not a software fallback. For the deeper "do the rendered
pixels match the claimed GPU class" check, route paints through the canvas bridge.
"""

# Shared probe JS - reads VENDOR/RENDERER + the unmasked pair via a throwaway WebGL context, with a
# graceful fallback if WebGL is unavailable. Returned to the SDK as a plain dict.
PROBE_JS = r"""
() => {
  const out = { webgl: false, webgl2: false, vendor: "", renderer: "",
                unmaskedVendor: "", unmaskedRenderer: "", maxTextureSize: 0 };
  try {
    const c = document.createElement('canvas');
    const gl2 = c.getContext('webgl2');
    const gl = gl2 || c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) return out;
    out.webgl = true;
    out.webgl2 = !!gl2;
    out.vendor = gl.getParameter(gl.VENDOR) || "";
    out.renderer = gl.getParameter(gl.RENDERER) || "";
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    if (dbg) {
      out.unmaskedVendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) || "";
      out.unmaskedRenderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) || "";
    }
    out.maxTextureSize = gl.getParameter(gl.MAX_TEXTURE_SIZE) || 0;
    out.maxRenderbufferSize = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE) || 0;
    const vp = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
    out.maxViewportDims = vp ? Array.from(vp) : [];
    out.maxVertexUniformVectors = gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS) || 0;
    out.maxFragmentUniformVectors = gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS) || 0;
    // Capability probe, not a declared value: a spoofed renderer string is free, an actual
    // 16384-wide texture allocation is not. A software-rasterizer backend refuses it.
    // The error queue is drained first: getError() is sticky, so a flag left behind by anything
    // above would otherwise be read back as "the allocation failed".
    try {
      let drain = 0;
      while (gl.getError() !== gl.NO_ERROR && drain++ < 32) {}
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 16384, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      out.canAllocate16k = gl.getError() === gl.NO_ERROR;
      gl.deleteTexture(t);
    } catch (e) { out.canAllocate16k = null; }
  } catch (e) { out.error = String(e); }
  return out;
}
"""

_SOFTWARE_MARKERS = (
    "swiftshader", "google swiftshader", "llvmpipe", "softpipe",
    "mesa offscreen", "microsoft basic render", "software adapter",
)

# substring -> GPU family. Order matters only in that the first hit wins per string.
_FAMILY_KEYS = (
    ("nvidia", "nvidia"), ("geforce", "nvidia"), ("rtx", "nvidia"), ("gtx", "nvidia"), ("quadro", "nvidia"),
    ("radeon", "amd"), ("amd", "amd"), ("ati ", "amd"),
    ("intel", "intel"), ("iris", "intel"), ("uhd graphics", "intel"), ("hd graphics", "intel"),
    ("apple", "apple"), ("m1", "apple"), ("m2", "apple"), ("m3", "apple"), ("m4", "apple"),
    ("mali", "mali"), ("adreno", "adreno"), ("powervr", "powervr"),
)

# Desktop/laptop GPU families. A persona that names one of these is claiming a machine whose
# driver backs a 16384 texture; the mobile families (mali/adreno/powervr) legitimately sit below
# that, so the capability floor below is not applied to them.
_DESKTOP_FAMILIES = ("nvidia", "amd", "intel", "apple")

# Capability floor for a desktop-class GPU claim, measured rather than assumed:
#   SwiftShader (ANGLE/Vulkan, genuine Chrome 153 headless Linux) ....... 8192
#   Mesa llvmpipe (LLVM 15, genuine Chrome 153 headed Linux) ........... 16384
#   ANGLE/D3D11 on a GeForce RTX 3070 (genuine Chrome 153 Windows) ..... 16384
# 16384 is also the D3D11 feature-level-11 2D texture cap (every desktop GPU since ~2010) and the
# Mesa GL limit for Intel Gen7+. So a renderer string naming a desktop GPU while MAX_TEXTURE_SIZE
# is below 16384 means the string was spoofed over a software rasterizer that _SOFTWARE_MARKERS
# can no longer see -- the exact case the string check misses once the persona renames the backend.
_HW_MIN_MAX_TEXTURE_SIZE = 16384


def _family(s):
    """Best-effort GPU family from a vendor/renderer string ('' if unknown)."""
    s = (s or "").lower()
    for key, fam in _FAMILY_KEYS:
        if key in s:
            return fam
    return ""


def evaluate_render_info(info, claimed_gpu=None):
    """Pure analysis of a probe result dict -> coherence verdict (unit-testable, no Playwright)."""
    renderer = info.get("unmaskedRenderer") or info.get("renderer") or ""
    vendor = info.get("unmaskedVendor") or info.get("vendor") or ""
    rl, vl = renderer.lower(), vendor.lower()
    warnings = []

    has_webgl = bool(info.get("webgl"))
    if not has_webgl:
        warnings.append(
            "WebGL is unavailable - a hard tell for a real desktop browser (only headless or "
            "locked-down setups disable it)."
        )

    software = any(m in rl or m in vl for m in _SOFTWARE_MARKERS)
    if software:
        warnings.append(
            f"software rasterizer detected in the WebGL renderer ({renderer!r}) - a definitive "
            "headless/no-GPU tell. Enable the canvas bridge (canvas_bridge=...) or run headed on a "
            "machine with a real GPU."
        )

    rfam, vfam = _family(rl), _family(vl)

    # Capability floor. The string check above is defeated the moment the persona renames the
    # backend, so verify the claim against a limit the renderer string cannot move: a software
    # rasterizer reports (and allocates) 8192 where every desktop GPU does 16384.
    max_tex = info.get("maxTextureSize") or 0
    can_16k = info.get("canAllocate16k")
    if rfam in _DESKTOP_FAMILIES and max_tex and max_tex < _HW_MIN_MAX_TEXTURE_SIZE:
        software = True
        warnings.append(
            f"the WebGL renderer names a desktop GPU ({renderer!r}) but MAX_TEXTURE_SIZE is "
            f"{max_tex}, below the {_HW_MIN_MAX_TEXTURE_SIZE} current desktop drivers report "
            "(only pre-feature-level-11 D3D parts cap at 8192) - on a stealth build this means the "
            "renderer string was spoofed over a software rasterizer (headless Linux falls back to "
            "SwiftShader). Run headed, or use the canvas bridge."
        )
    elif rfam in _DESKTOP_FAMILIES and can_16k is False:
        software = True
        warnings.append(
            f"the WebGL renderer names a desktop GPU ({renderer!r}) and reports MAX_TEXTURE_SIZE "
            f"{max_tex}, but a {_HW_MIN_MAX_TEXTURE_SIZE}-wide texture fails to allocate - the "
            "reported limit is not backed by the real rendering backend."
        )

    # Uniform-vector split. Pass-through and persona-spoofed limits can end up on different sides
    # of the same context: an ANGLE-over-GL/Mesa renderer reports vertex == fragment on every real
    # driver (measured: SwiftShader 4096/4096, llvmpipe 1024/1024), so a mismatch under a GL-backed
    # renderer string is a half-applied persona. ANGLE/D3D11 legitimately differs (4095/1024), so
    # the check is limited to the non-D3D backends.
    vuv = info.get("maxVertexUniformVectors") or 0
    fuv = info.get("maxFragmentUniformVectors") or 0
    incoherent = False
    if vuv and fuv and vuv != fuv and "d3d" not in rl and "direct3d" not in rl:
        incoherent = True
        warnings.append(
            f"MAX_VERTEX_UNIFORM_VECTORS ({vuv}) and MAX_FRAGMENT_UNIFORM_VECTORS ({fuv}) disagree "
            f"under a non-D3D renderer ({renderer!r}); every GL backend measured here reports them "
            "equal (SwiftShader 4096/4096, llvmpipe 1024/1024), so the persona applied to one and "
            "not the other."
        )

    if rfam and vfam and rfam != vfam:
        incoherent = True
        warnings.append(
            f"WebGL vendor and renderer disagree on GPU family (vendor~{vfam}, renderer~{rfam}) - "
            "an incoherent persona."
        )

    if claimed_gpu:
        cfam = _family(claimed_gpu)
        if cfam and rfam and cfam != rfam:
            incoherent = True
            warnings.append(
                f"the claimed GPU ({claimed_gpu!r}, family ~{cfam}) does not match the WebGL "
                f"renderer family (~{rfam})."
            )

    # Set by the branches above rather than by matching warning text: the verdict must not depend
    # on the wording of a message.
    coherent = has_webgl and not software and not incoherent
    return {
        "vendor": vendor,
        "renderer": renderer,
        "webgl": has_webgl,
        "webgl2": bool(info.get("webgl2")),
        "max_texture_size": info.get("maxTextureSize") or 0,
        "max_renderbuffer_size": info.get("maxRenderbufferSize") or 0,
        "max_vertex_uniform_vectors": vuv,
        "max_fragment_uniform_vectors": fuv,
        "can_allocate_16k_texture": can_16k,
        "software_suspected": software,
        "coherent": coherent,
        "warnings": warnings,
    }


def check_render_coherence(page, claimed_gpu=None):
    """Probe a live Playwright ``page`` for render-backend coherence (#7).

    Returns a dict with ``vendor``/``renderer`` (the values the page actually sees),
    ``software_suspected`` (bool - a SwiftShader/llvmpipe fallback is a fatal tell), ``coherent``
    (bool), and human-readable ``warnings``. Pass ``claimed_gpu`` (the GPU string your persona is
    supposed to present) to additionally assert the rendered family matches.

    Example::

        br = clearcote.launch(fingerprint="77")
        page = br.new_page(); page.goto("about:blank")
        verdict = clearcote.check_render_coherence(page)
        assert verdict["coherent"], verdict["warnings"]
    """
    info = page.evaluate(PROBE_JS)
    return evaluate_render_info(info, claimed_gpu)
