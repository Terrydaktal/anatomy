"""Curated source-object identities; paired bones remain separate catalogue leaves."""

from __future__ import annotations


GROUPS = [
    {"id": "cranial", "name": "Cranial bones", "description": "8 bones · brain case and cranial base"},
    {"id": "facial", "name": "Facial bones", "description": "14 bones · face, palate and nasal cavity"},
    {"id": "ossicles", "name": "Auditory ossicles", "description": "6 bones · three in each middle ear"},
    {"id": "teeth", "name": "Teeth", "description": "28 teeth · third molars absent from this atlas"},
    {"id": "associated", "name": "Associated bone", "description": "Hyoid · separate from the skull"},
]

# name, source name, group, colour (or left/right colours), description, paired
STRUCTURES = [
    ("Frontal bone", "Frontal bone", "cranial", "#e8b45d", "Forms the forehead, roofs of the orbits and the anterior cranial floor.", False),
    ("Parietal bone", "Parietal bone", "cranial", ("#e78f9c", "#79b4e5"), "Forms the upper lateral wall and roof of the cranial vault.", True),
    ("Temporal bone", "Temporal bone", "cranial", ("#ac99dd", "#7bb69a"), "Forms part of the lateral skull and cranial base. Its petrous part surrounds the middle and inner ear.", True),
    ("Occipital bone", "Occipital bone", "cranial", "#63bec7", "Forms the back and lower part of the cranium and surrounds the foramen magnum.", False),
    ("Sphenoid bone", "Sphenoid bone", "cranial", "#bac95f", "Spans the central cranial base. Its wings contribute to the orbits and the sides of the skull.", False),
    ("Ethmoid bone", "Ethmoid bone", "cranial", "#dc92c1", "Lies between the orbits. Contributes to the nasal septum, nasal cavity and anterior cranial floor.", False),
    ("Maxilla", "Maxilla", "facial", ("#edaa7d", "#80caca"), "Forms the upper jaw and part of the hard palate, nasal cavity and orbital floor. Supports the upper teeth.", True),
    ("Zygomatic bone", "Zygomatic bone", "facial", ("#90bd77", "#bb98cb"), "Forms the prominence of the cheek and contributes to the lateral orbital wall and zygomatic arch.", True),
    ("Nasal bone", "Nasal bone", "facial", ("#e5cf73", "#8b98df"), "Forms the bony bridge of the nose.", True),
    ("Lacrimal bone", "Lacrimal bone", "facial", ("#a3cacc", "#d6a38e"), "Small bone in the medial orbital wall, beside the groove for the lacrimal sac.", True),
    ("Palatine bone", "Palatine bone", "facial", ("#c49b65", "#91b0d0"), "Forms the posterior hard palate and contributes to the nasal cavity and a small part of the orbit.", True),
    ("Inferior nasal concha", "Inferior nasal concha bone", "facial", ("#d9b6ce", "#b4cb8d"), "A separate curled bone on the lateral wall of the nasal cavity. The middle and superior conchae belong to the ethmoid.", True),
    ("Vomer", "Vomer", "facial", "#b8a9dc", "Forms the lower and posterior part of the bony nasal septum.", False),
    ("Mandible", "Mandible", "facial", "#b8bea6", "Forms the lower jaw and supports the lower teeth. Its condyles articulate with the temporal bones.", False),
    ("Malleus", "Malleus", "ossicles", ("#dfaa59", "#7d9fcd"), "The hammer-shaped middle-ear ossicle. Its handle attaches to the tympanic membrane and its head articulates with the incus.", True),
    ("Incus", "Incus", "ossicles", ("#88c3b0", "#ce8f7a"), "The anvil-shaped middle-ear ossicle between the malleus and stapes.", True),
    ("Stapes", "Stapes", "ossicles", ("#d0a1b4", "#b4c183"), "The stirrup-shaped middle-ear ossicle. Its footplate transmits vibration to the oval window.", True),
    ("Hyoid bone", "Hyoid bone", "associated", "#bda880", "A separate bone below the mandible that supports the tongue and provides muscle attachments. It does not articulate directly with the skull.", False),
]


def catalogue() -> list[dict]:
    entries = []
    for name, source, group, colors, description, paired in STRUCTURES:
        for side in (["left", "right"] if paired else ["midline"]):
            suffix = f".{side[0]}" if paired else ""
            slug = name.lower().replace(" ", "-")
            entries.append({
                "id": f"{slug}-{side}" if paired else slug,
                "name": f"{side.capitalize()} {name.lower()}" if paired else name,
                "source_name": source + suffix,
                "group": group,
                "side": side,
                "kind": "bone",
                "color": colors[0 if side == "left" else 1] if paired else colors,
                "description": description,
                "visible": True,
            })
    tooth_types = [
        ("medial incisor", "central incisor"),
        ("lateral incisor", "lateral incisor"),
        ("canine", "canine"),
        ("first premolar", "first premolar"),
        ("second premolar", "second premolar"),
        ("first molar tooth", "first molar"),
        ("second molar tooth", "second molar"),
    ]
    for arch in ["Upper", "Lower"]:
        for side in ["left", "right"]:
            for source, name in tooth_types:
                label = f"{arch} {side} {name}"
                entries.append({
                    "id": label.lower().replace(" ", "-"),
                    "name": label,
                    "source_name": f"{arch} {source}.{side[0]}",
                    "group": "teeth",
                    "side": side,
                    "kind": "tooth",
                    "arch": arch.lower(),
                    "color": "#f1e9d5",
                    "description": f"Permanent {name} in the {side} half of the {arch.lower()} dental arch. Teeth are shown separately from the bone count. Third molars are absent from this source atlas.",
                    "visible": True,
                })
    return entries
