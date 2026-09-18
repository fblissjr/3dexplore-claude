"""Finding a bold font, without hardcoding one distro's layout.

The old code pointed at a single Linux path, which meant the text CLI died on
a Mac. Fonts are a lookup, not a constant.
"""
import glob, os

# Roughly in order of how much they look like a name plaque.
PREFERRED = ("Poppins-Bold", "DejaVuSans-Bold", "Arial Bold", "Helvetica",
             "LiberationSans-Bold", "SFNSDisplay")

ROOTS = ("/usr/share/fonts", "/usr/local/share/fonts",
         "/System/Library/Fonts", "/Library/Fonts",
         os.path.expanduser("~/Library/Fonts"),
         os.path.expanduser("~/.fonts"),
         "C:/Windows/Fonts")


def bold_font(prefer=PREFERRED):
    """First available bold TrueType, by preference then by any *Bold*."""
    found = []
    for root in ROOTS:
        if os.path.isdir(root):
            found += glob.glob(f"{root}/**/*.tt[fc]", recursive=True)
    for want in prefer:
        for path in found:
            if want.lower() in os.path.basename(path).lower():
                return path
    for path in found:
        if "bold" in os.path.basename(path).lower():
            return path
    if not found:
        raise FileNotFoundError(
            "no TrueType fonts found in " + ", ".join(ROOTS) + " -- pass --font")
    return found[0]
