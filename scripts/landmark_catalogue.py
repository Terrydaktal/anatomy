"""Bone-only study terminology and short anatomical descriptions.

Names follow the pinned Z-Anatomy labels and public-domain FIPAT TA2 terms.
Descriptions are original study notes. Coordinates in CURATED are approximate
reference-skull study locations in millimetres, never replacement anatomy.
"""

from __future__ import annotations


TERMINOLOGY_URL = "https://cdn.dal.ca/content/dam/dalhousie/pdf/library/FIPAT/TA2/FIPAT-TA2-Part-2.pdf"
OVERVIEW_URL = "https://openstax.org/books/anatomy-and-physiology-2e/pages/7-2-the-skull"

CATEGORIES = [
    {"id": "sutures", "name": "Sutures"},
    {"id": "foramina", "name": "Foramina and openings"},
    {"id": "canals", "name": "Canals and meatuses"},
    {"id": "fissures", "name": "Fissures"},
    {"id": "processes", "name": "Processes and projections"},
    {"id": "articulations", "name": "Articular surfaces"},
    {"id": "parts", "name": "Bone parts and surfaces"},
    {"id": "ridges", "name": "Crests, lines and ridges"},
    {"id": "notches", "name": "Notches"},
    {"id": "grooves", "name": "Grooves and sulci"},
    {"id": "sinuses", "name": "Bony sinuses and air cells"},
    {"id": "fossae", "name": "Fossae and depressions"},
    {"id": "landmarks", "name": "Junctions and reference points"},
]

# Source annotations can be moved well away from the feature for legibility.
# These explicitly curated left-side/midline study locations replace a projected
# text leader when its distance exceeds 10 mm. The original leader is retained
# in the exported provenance, never silently presented as an exact surface mark.
SOURCE_LOCATIONS = {
    "Base of mandible": [0, -102, 72],
    "Body of mandible": [0, -85, 70],
    "Body of sphenoid bone": [0, -18, 27],
    "Mandibular symphysis": [0, -91, 80],
    "Dorsum sellae": [0, 4, 14],
    "Ethmoidal notch": [0, 0, 55],
    "External occipital protuberance": [0, -6, -101],
    "External surface of parietal bone": [70, 42, -28],
    "External surface of squamous part of frontal bone": [0, 47, 72],
    "Foramen magnum": [0, -43, -27],
    "Foramen rotundum": [17, -5, 39],
    "Frontal angle of parietal bone": [1, 98, 24],
    "Frontal border of parietal bone": [51, 65, 31],
    "Frontal part of orbital margin": [27, 0, 79],
    "Genion": [0, -90, 69],
    "Glabella": [0, 9, 86],
    "Gnathion": [0, -103, 80],
    "Greater horn of hyoid bone": [20, -99, 11],
    "Groove for marginal sinus": [12, -40, -25],
    "Groove for middle meningeal artery": [56, 25, -6],
    "Groove of pterygoid hamulus": [10, -49, 29],
    "Inferior mental spine": [0, -94, 68],
    "Inferior temporal line": [69, 32, -11],
    "Infratemporal surface of greater wing": [41, -19, 26],
    "Internal occipital crest": [0, -20, -64],
    "Internal surface of parietal bone": [61, 42, -30],
    "Internal surface of squamous part of frontal bone": [0, 40, 64],
    "Lateral part of occipital bone": [23, -37, -24],
    "Lateral pterygoid plate": [20, -39, 32],
    "Mandibular dental arch": [25, -71, 58],
    "Mastoid angle of parietal bone": [57, -7, -50],
    "Mastoid border of occipital bone": [43, -26, -49],
    "Mastoid process": [53, -49, -26],
    "Maxillary dental arch": [24, -59, 63],
    "Maxillary surface of greater wing": [28, -7, 43],
    "Mental foramen": [24, -88, 71],
    "Mental protuberance": [0, -96, 84],
    "Mylohyoid line": [26, -77, 52],
    "Nasal margin of frontal bone": [0, 0, 84],
    "Nasal part of frontal bone": [0, 0, 78],
    "Nasal spine of frontal bone": [0, -6, 78],
    "Oblique line of mandible": [33, -73, 46],
    "Occipital angle of parietal bone": [1, 53, -96],
    "Occipital border of parietal bone": [42, 34, -83],
    "Occipital margin of temporal bone": [51, -23, -43],
    "Occipital plane": [0, 24, -100],
    "Opening of frontal sinus": [7, -2, 69],
    "Orbital part of frontal bone": [27, -2, 60],
    "Orbital plate of frontal bone": [27, -2, 60],
    "Orbital surface of frontal bone": [27, -4, 65],
    "Parietal eminence": [73, 39, -34],
    "Parietal margin of greater wing": [52, 10, 30],
    "Posterior clinoid process": [8, 8, 13],
    "Pterygoid canal": [13, -25, 27],
    "Pterygoid hamulus": [10, -49, 30],
    "Pterygoid notch": [15, -46, 32],
    "Pterygoid process": [16, -36, 31],
    "Pterygospinous process": [24, -36, 20],
    "Sagittal border of parietal bone": [1, 102, -27],
    "Scaphoid fossa": [12, -26, 25],
    "Sphenoidal angle of parietal bone": [53, 18, 34],
    "Sphenoidal crest": [0, -7, 45],
    "Sphenoidal lingula": [16, -18, 10],
    "Spine of sphenoid bone": [35, -25, 8],
    "Squamosal border of parietal bone": [70, 4, -6],
    "Squamosal margin of greater wing": [51, -6, 20],
    "Squamous part of frontal bone": [0, 53, 70],
    "Squamous part of occipital bone": [0, 15, -101],
    "Squamous part of temporal bone": [67, 4, -1],
    "Sulcus of auditory tube": [23, -27, 12],
    "Superciliary arch": [23, 7, 81],
    "Superior mental spine": [0, -89, 68],
    "Superior temporal line": [68, 46, -12],
    "Supra-orbital notch": [25, 1, 78],
    "Supreme nuchal line": [22, 2, -98],
    "Temporal line": [51, 20, 68],
    "Temporal surface of frontal bone": [55, 20, 46],
    "Temporal surface of greater wing": [48, 0, 36],
    "Tympanic part of temporal bone": [55, -27, 0],
    "Vaginal process of sphenoid bone": [8, -29, 26],
    "Zygomatic margin of greater wing": [41, 2, 50],
    "Zygomatic process of frontal bone": [42, 1, 59],
}

# Source label name | category | paired in an unpaired bone | useful view | description.
# A paired owner (for example the temporal bone) is handled by the exporter.
SOURCE_NOTES = """
External occipital crest|ridges|0|posterior|Midline ridge on the external occipital surface, running toward the foramen magnum from the external occipital protuberance.
Internal occipital crest|ridges|0|superior|Midline ridge inside the occipital bone, descending from the internal occipital protuberance toward the foramen magnum.
Mandibular symphysis|landmarks|0|anterior|Adult midline fusion site between the two developmental halves of the mandibular body, at the chin.
Middle clinoid process|processes|1|superior|Variable small projection beside the hypophyseal fossa on the body of the sphenoid.
Supra-orbital notch|notches|1|anterior|Notch in the superior orbital rim of the frontal bone; the supraorbital nerve and vessels pass at this site.
Trochlear spine|processes|1|inferior|Small variable projection near the medial orbital roof, beside the attachment of the superior oblique pulley.
Vermian fossa|fossae|0|superior|Small midline depression on the endocranial occipital surface behind the foramen magnum.
Anterior clinoid process|processes|1|superior|Posteromedial projection of the lesser sphenoidal wing beside the optic canal and anterior to the sella turcica.
Anterior limb of stapes|parts|0|anterior|Anterior bony crus linking the head of the stapes to its footplate in the middle ear.
Anterior process of malleus|processes|0|anterior|Slender projection from the anterior malleus near its neck in the middle ear.
Anterior surface of petrous part|parts|0|superior|Forward-facing petrous temporal surface forming part of the floor of the middle cranial fossa.
Apex of petrous part|parts|0|superior|Medial tip of the petrous temporal pyramid, directed toward the sphenoid and foramen lacerum.
Arcuate eminence|processes|0|superior|Rounded elevation on the anterior surface of the petrous temporal bone above the bony labyrinth.
Articular facet for malleus|articulations|0|anterior|Saddle-shaped surface on the incus body that meets the head of the malleus.
Articular facet for stapes|articulations|0|anterior|Small joint surface on the incus lenticular process that meets the stapes head.
Articular facet of head of malleus|articulations|0|anterior|Joint surface on the head of the malleus where it articulates with the body of the incus.
Articular facet of head of stapes|articulations|0|anterior|Small surface on the stapes head receiving the lenticular process of the incus.
Base of mandible|parts|0|anterior|Thick inferior border of the mandibular body, beneath the tooth-bearing alveolar part.
Base of stapes|parts|0|anterior|Oval bony footplate of the stapes seated in the vestibular window of the temporal bone.
Basilar part of occipital bone|parts|0|inferior|Occipital segment anterior to the foramen magnum; its cranial surface contributes to the clivus.
Body of hyoid bone|parts|0|anterior|Central, transversely oriented part of the hyoid between the greater and lesser horns.
Body of incus|parts|0|anterior|Rounded central portion of the incus that articulates with the malleus and gives rise to its two limbs.
Body of mandible|parts|0|anterior|Horizontal horseshoe-shaped portion of the lower jaw, carrying the lower dental sockets.
Body of sphenoid bone|parts|0|superior|Central sphenoidal mass between the wings, containing the sphenoidal sinus and supporting the sella turcica.
Carotid sulcus|grooves|1|superior|Groove along the lateral sphenoid body beside the sella turcica, related to the cavernous course of the internal carotid artery.
Cerebellar fossa|fossae|1|superior|Lower internal occipital depression that accommodates a cerebellar hemisphere.
Cerebral fossa|fossae|1|superior|Upper internal occipital depression related to the occipital lobe of the cerebral hemisphere.
Cerebral surface of greater wing|parts|1|superior|Concave upper surface of the greater sphenoidal wing forming the lateral floor of the middle cranial fossa.
Chiasmatic sulcus|grooves|0|superior|Transverse groove on the sphenoid in front of the tuberculum sellae, connecting the regions of the optic canals.
Condylar canal|canals|1|inferior|Variable short passage posterior to an occipital condyle, transmitting an emissary vein when present.
Condylar fossa|fossae|1|inferior|Depression immediately behind an occipital condyle, sometimes containing the external opening of a condylar canal.
Cruciform eminence|ridges|0|superior|Cross-shaped arrangement of ridges on the internal occipital surface, centred on the internal occipital protuberance.
Digastric fossa|fossae|1|inferior|Shallow depression on the inferior inner mandibular body near the midline for the anterior digastric attachment.
Dorsum sellae|parts|0|superior|Posterior bony wall of the sella turcica, ending superiorly in the posterior clinoid processes.
Ethmoidal notch|notches|0|superior|Midline gap between the orbital plates of the frontal bone that receives the ethmoid.
External occipital protuberance|processes|0|posterior|Midline prominence on the back of the occipital bone, above the external occipital crest.
External surface of parietal bone|parts|0|left|Convex outer parietal surface, bearing the parietal eminence and temporal lines.
External surface of squamous part of frontal bone|parts|0|anterior|Outer forehead surface of the frontal squama, above the orbital rims.
Foramen caecum of frontal bone|foramina|0|superior|Small midline opening or pit between the frontal crest and ethmoid crista galli; it may be closed in the adult.
Foramen magnum|foramina|0|inferior|Large occipital opening connecting the cranial cavity with the vertebral canal.
Foramen ovale|foramina|1|inferior|Oval opening in the greater sphenoidal wing, posterolateral to the foramen rotundum.
Foramen rotundum|foramina|1|superior|Round passage through the greater sphenoidal wing linking the middle cranial fossa and pterygopalatine fossa.
Foramen spinosum|foramina|1|inferior|Small opening in the greater sphenoidal wing, posterolateral to the foramen ovale near the sphenoidal spine.
Fossa for lacrimal gland|fossae|1|inferior|Depression in the lateral orbital roof of the frontal bone that accommodates the lacrimal gland.
Frontal angle of parietal bone|parts|0|superior|Anterosuperior corner of the parietal bone at the junction of the coronal and sagittal sutures.
Frontal border of parietal bone|parts|0|anterior|Anterior parietal edge that joins the frontal bone along the coronal suture.
Frontal crest|ridges|0|superior|Midline ridge on the internal frontal surface, providing an anterior attachment for the falx cerebri.
Frontal eminence|processes|1|anterior|Rounded prominence on the forehead surface of the frontal bone, above the superciliary arch.
Frontal margin of greater wing|parts|1|anterior|Superior edge of the greater sphenoidal wing where it meets the frontal bone.
Genion|landmarks|0|posterior|Midline reference point on the inner mandibular symphysis in the region of the mental spines.
Glabella|landmarks|0|anterior|Smooth midline prominence between the superciliary arches, above the root of the nose.
Gnathion|landmarks|0|anterior|Midline reference point on the most anteroinferior contour of the mandibular chin.
Greater horn of hyoid bone|processes|1|inferior|Long posterior projection from each side of the hyoid body.
Greater wing|parts|1|left|Large lateral extension of the sphenoid contributing to the cranial floor, lateral skull and posterior orbital wall.
Groove for marginal sinus|grooves|0|superior|Vascular groove on the internal occipital surface around the margin of the foramen magnum.
Groove for middle meningeal artery|grooves|0|superior|Branching arterial impressions on the internal parietal surface.
Groove for occipital sinus|grooves|0|superior|Midline vascular impression beside the internal occipital crest.
Groove for transverse sinus|grooves|1|superior|Transverse vascular impression on the internal occipital surface, extending laterally from the internal occipital protuberance.
Groove of pterygoid hamulus|grooves|1|inferior|Small groove around the hook of the medial pterygoid plate, where the tensor veli palatini tendon changes direction.
Handle of malleus|parts|0|anterior|Elongated malleus segment that lies against the tympanic membrane.
Head of malleus|parts|0|anterior|Expanded superior portion of the malleus that articulates with the incus.
Hypoglossal canal|canals|1|inferior|Bony tunnel through the lateral occipital bone immediately above an occipital condyle.
Hypophysial fossa|fossae|0|superior|Central depression of the sella turcica in the sphenoid body, housing the pituitary gland.
Inferior mental spine|processes|0|posterior|Lower midline projection on the inner mandibular symphysis, serving the geniohyoid attachment.
Inferior nuchal line|ridges|0|posterior|Curved occipital ridge below the superior nuchal line on the external posterior skull base.
Inferior temporal line|ridges|0|left|Lower curved temporal line on the parietal surface, marking the upper extent of temporalis attachment.
Infratemporal surface of greater wing|parts|1|inferior|Inferior aspect of the greater sphenoidal wing below the infratemporal crest.
Internal occipital protuberance|processes|0|superior|Central elevation on the internal occipital surface where the arms of the cruciform eminence meet.
Internal surface of parietal bone|parts|0|superior|Concave parietal surface facing the cranial cavity, with arterial grooves and cerebral impressions.
Internal surface of squamous part of frontal bone|parts|0|superior|Concave inner frontal squama facing the frontal lobes and bearing the frontal crest.
Intrajugular process of occipital bone|processes|1|inferior|Small projection into the occipital jugular notch, contributing to the partition of the jugular foramen.
Jugular notch of occipital bone|notches|1|inferior|Lateral indentation of the occipital bone forming the medial boundary of the jugular foramen.
Jugular process|processes|1|inferior|Lateral occipital projection beside the jugular foramen and occipital condyle.
Lateral process of malleus|processes|0|anterior|Short lateral malleus projection at the upper end of the handle.
Lateral pterygoid plate|parts|1|inferior|Broad outer plate of the sphenoid pterygoid process, lateral to the pterygoid fossa.
Lenticular process of incus|processes|0|anterior|Small terminal projection of the long incus limb that articulates with the stapes head.
Lesser horn of hyoid bone|processes|1|anterior|Small superior projection at the junction of the hyoid body and greater horn.
Lesser wing|parts|1|superior|Thin superior sphenoidal wing forming the posterior boundary of the anterior cranial fossa.
Limbus of sphenoid|ridges|0|superior|Anterior bony lip bordering the chiasmatic sulcus on the superior sphenoid body.
Long limb of incus|parts|0|anterior|Long inferior incus process that ends at the lenticular process beside the stapes.
Mandibular dental arch|parts|0|superior|Tooth-bearing curved alveolar region of the mandible surrounding the lower dental sockets.
Mastoid angle of parietal bone|parts|0|posterior|Posteroinferior parietal corner meeting the mastoid temporal and occipital bones near asterion.
Mastoid notch|notches|0|inferior|Groove-like indentation medial to the mastoid process, providing the posterior digastric attachment.
Mastoid process|processes|0|inferior|Inferior projection of the mastoid temporal region, posterior to the external acoustic meatus.
Maxillary dental arch|parts|0|inferior|Curved tooth-bearing alveolar region of the maxilla surrounding the upper dental sockets.
Maxillary surface of greater wing|parts|1|anterior|Anterior surface of the greater sphenoidal wing facing the posterior maxilla across the pterygopalatine region.
Medial pterygoid plate|parts|1|inferior|Thin inner plate of the sphenoid pterygoid process, ending below in the pterygoid hamulus.
Mental foramen|foramina|1|anterior|Opening on the anterolateral mandibular body, usually beneath the premolar region.
Mental protuberance|processes|0|anterior|Midline triangular prominence forming the anterior bony chin.
Mental tubercle|processes|1|anterior|Small prominence on either side of the inferior mental protuberance.
Mylohyoid line|ridges|1|posterior|Oblique ridge on the internal mandibular body for attachment of the mylohyoid muscle.
Nasal spine of frontal bone|processes|0|anterior|Midline inferior projection of the frontal nasal part, between the nasal bones and ethmoid.
Neck of malleus|parts|0|anterior|Narrow malleus segment between its rounded head and handle.
Oblique line of mandible|ridges|1|left|External mandibular ridge running forward from the anterior border of the ramus onto the body.
Occipital angle of parietal bone|parts|0|posterior|Posterosuperior parietal corner at the junction of the sagittal and lambdoid sutures.
Occipital condyle|articulations|1|inferior|Oval articular prominence beside the foramen magnum that meets the superior facet of the atlas.
Occipital margin of temporal bone|parts|0|posterior|Posterior mastoid edge of the temporal bone that joins the occipital bone.
Occipital plane|parts|0|posterior|External occipital surface above the superior nuchal lines, forming the back of the cranial vault.
Opening of frontal sinus|foramina|1|inferior|Bony drainage aperture in the frontal sinus floor toward the frontal recess.
Optic canal|canals|1|anterior|Short tunnel through the root of the lesser sphenoidal wing connecting the middle cranial fossa and orbit.
Orbital plate of frontal bone|parts|1|superior|Thin horizontal frontal plate that forms the orbital roof and supports the anterior cranial fossa.
Orbital surface of frontal bone|parts|1|inferior|Inferior surface of the frontal orbital plate, forming the roof of the orbit.
Orbital surface of greater wing|parts|1|anterior|Forward-facing greater-wing surface forming the posterior portion of the lateral orbital wall.
Parietal eminence|processes|0|left|Rounded prominence on the lateral external parietal surface.
Parietal foramen|foramina|0|superior|Small variable opening near the sagittal border of the posterior parietal bone.
Petrous part of temporal bone|parts|0|superior|Dense pyramidal temporal portion containing the bony inner ear and several cranial-base passages.
Pharyngeal tubercle|processes|0|inferior|Midline prominence on the inferior basilar occipital surface, anterior to the foramen magnum.
Posterior clinoid process|processes|1|superior|Projection from the superior lateral corner of the dorsum sellae.
Posterior limb of stapes|parts|0|anterior|Posterior bony crus linking the stapes head and footplate.
Posterior surface of petrous part|parts|0|superior|Petrous temporal surface facing the posterior cranial fossa, containing the internal acoustic opening.
Pterygoid canal|canals|1|inferior|Passage through the base of the sphenoid pterygoid process between the foramen-lacerum region and pterygopalatine fossa.
Pterygoid fossa|fossae|1|inferior|Space between the medial and lateral pterygoid plates on the posterior sphenoid pterygoid process.
Pterygoid hamulus|processes|1|inferior|Hook at the inferior end of the medial pterygoid plate.
Pterygoid notch|notches|1|inferior|Gap between the lower pterygoid plates that receives the pyramidal process of the palatine bone.
Scaphoid fossa|fossae|1|inferior|Small depression near the superior root of the medial pterygoid plate.
Sphenoidal angle of parietal bone|parts|0|left|Anteroinferior parietal corner near pterion, where frontal, temporal and sphenoidal bones meet.
Sphenoidal crest|ridges|0|anterior|Midline vertical ridge on the anterior sphenoid body that articulates with the perpendicular plate of the ethmoid.
Sphenoidal lingula|processes|1|superior|Small projection beside the sphenoidal carotid sulcus near the foramen lacerum.
Sphenoidal yoke|parts|0|superior|Flat superior sphenoid surface in front of the chiasmatic sulcus, linking the roots of the lesser wings.
Frontal part of orbital margin|ridges|1|anterior|Superior orbital rim contributed by the frontal bone.
Lateral part of occipital bone|parts|1|inferior|Occipital segment on either side of the foramen magnum, bearing a condyle and hypoglossal canal.
Mastoid border of occipital bone|parts|1|posterior|Lateral occipital edge adjoining the mastoid portion of the temporal bone.
Nasal margin of frontal bone|parts|0|anterior|Inferior frontal edge that joins the nasal bones and frontal processes of the maxillae.
Nasal part of frontal bone|parts|0|anterior|Midline inferior frontal region between the orbital parts, above the root of the nose.
Occipital border of parietal bone|parts|0|posterior|Posterior parietal edge that articulates with the occipital bone along the lambdoid suture.
Orbital part of frontal bone|parts|1|inferior|Horizontal frontal region forming the orbital roof and part of the anterior cranial floor.
Parietal margin of frontal bone|parts|1|superior|Posterior edge of the frontal squama that meets the parietal bone at the coronal suture.
Parietal margin of greater wing|parts|1|left|Short superior greater-wing edge articulating with the sphenoidal angle of the parietal bone.
Pterygoid process|processes|1|inferior|Inferior sphenoidal projection formed by medial and lateral pterygoid plates.
Pterygospinous process|processes|1|inferior|Small projection on the posterior edge of the lateral pterygoid plate toward the sphenoidal spine.
Sagittal border of parietal bone|parts|0|superior|Superior parietal edge that joins the opposite parietal bone at the sagittal suture.
Sella turcica|parts|0|superior|Saddle-shaped superior sphenoid region composed of the tuberculum sellae, hypophyseal fossa and dorsum sellae.
Septum of frontal sinuses|parts|0|anterior|Bony partition separating the right and left frontal sinus cavities; its position is individually variable.
Septum of sphenoidal sinuses|parts|0|anterior|Bony partition between sphenoidal sinus compartments; it is often asymmetric.
Sphenoidal margin of frontal bone|parts|1|superior|Posterior frontal orbital edge that articulates with the sphenoid.
Spine of sphenoid bone|processes|1|inferior|Inferior-pointing projection near the posterolateral greater wing, behind the foramen spinosum.
Squamosal border of parietal bone|parts|0|left|Inferior beveled parietal edge overlapped by the squamous temporal bone.
Squamosal margin of greater wing|parts|1|left|Posterolateral greater-wing edge articulating with the squamous temporal bone.
Squamous part of frontal bone|parts|0|anterior|Upright broad frontal plate forming the forehead and anterior cranial vault.
Squamous part of occipital bone|parts|0|posterior|Broad occipital plate behind the foramen magnum, bearing internal and external occipital markings.
Squamous part of temporal bone|parts|0|left|Thin flat temporal plate forming part of the lateral cranial wall above the external acoustic meatus.
Styloid process of temporal bone|processes|0|inferior|Slender inferior temporal projection anterior to the mastoid process.
Sublingual fossa|fossae|1|posterior|Depression on the inner anterior mandibular body above the mylohyoid line.
Sulcus of auditory tube|grooves|1|inferior|Groove near the junction of the sphenoid and petrous temporal bone associated with the auditory tube.
Superciliary arch|ridges|1|anterior|Brow ridge on the frontal bone immediately above the medial orbital rim.
Superior mental spine|processes|0|posterior|Upper midline projection on the inner mandibular symphysis, serving the genioglossus attachment.
Superior nuchal line|ridges|0|posterior|Curved ridge extending laterally from the external occipital protuberance across the posterior skull.
Superior temporal line|ridges|0|left|Upper temporal line on the parietal bone marking the attachment of the temporal fascia.
Supreme nuchal line|ridges|0|posterior|Highest and often faint curved nuchal ridge above the superior nuchal line.
Tegmen tympani|parts|0|superior|Thin petrous temporal plate forming the bony roof of the tympanic cavity.
Temporal line|ridges|1|left|Curved frontal ridge that continues posteriorly into the temporal lines on the parietal bone.
Temporal surface of frontal bone|parts|1|left|Lateral frontal surface below the temporal line, contributing to the temporal fossa.
Temporal surface of greater wing|parts|1|left|Lateral sphenoidal greater-wing surface above the infratemporal crest.
Trochlear fovea|fossae|1|inferior|Small depression near the medial frontal orbital roof for the superior oblique pulley attachment.
Tuberculum sellae|ridges|0|superior|Raised anterior boundary of the hypophyseal fossa immediately behind the chiasmatic sulcus.
Tympanic part of temporal bone|parts|0|left|Curved temporal plate forming much of the anterior and inferior walls of the bony external acoustic meatus.
Vaginal process of sphenoid bone|processes|1|inferior|Thin medial projection near the root of the medial pterygoid plate, adjacent to the vomer.
Zygomatic margin of greater wing|parts|1|anterior|Anterior-lateral edge of the greater sphenoidal wing that meets the zygomatic bone.
Zygomatic process of frontal bone|processes|1|anterior|Lateral end of the frontal orbital rim that meets the frontal process of the zygomatic bone.
"""


# Name | category | owners (comma-separated; paired bone bases expand per side)
# | reference point for the left side or midline | view | paired | original description.
CURATED = """
Carotid canal|canals|temporal-bone|31,-36,-4|inferior|1|Bony passage through the petrous temporal bone with an inferior opening anterior to the jugular fossa and a medial opening near the petrous apex.
Internal acoustic meatus|canals|temporal-bone|32,-12,-20|superior|1|Short passage entering the posterior petrous temporal surface; its fundus leads toward the bony inner-ear and facial-nerve passages.
External acoustic meatus|canals|temporal-bone|61,-23,-4|left|1|Bony ear canal opening laterally in the temporal bone between its tympanic and squamous regions.
Facial canal|canals|temporal-bone|43,-22,-8|superior|1|Small intratemporal bony passage from the internal acoustic meatus toward the stylomastoid foramen. This study pin does not reconstruct its internal course.
Musculotubal canal|canals|temporal-bone|29,-25,7|inferior|1|Passage in the anterior petrous temporal region containing the bony auditory-tube and tensor-tympani semicanals.
Canal for tensor tympani|canals|temporal-bone|32,-24,5|superior|1|Upper compartment of the musculotubal canal in the petrous temporal bone. The internal tunnel is not separately segmented.
Bony auditory tube|canals|temporal-bone|30,-28,7|inferior|1|Inferior semicanal of the musculotubal canal opening toward the tympanic cavity.
Tympanic canaliculus|canals|temporal-bone|34,-35,-8|inferior|1|Small temporal-bone passage beginning in the ridge between the carotid canal and jugular fossa.
Mastoid canaliculus|canals|temporal-bone|40,-35,-17|inferior|1|Small passage from the jugular-fossa region toward the tympanomastoid fissure; its lumen is not separately segmented.
Cochlear canaliculus|canals|temporal-bone|27,-31,-11|inferior|1|Small canal in the inferior petrous temporal bone near the jugular fossa, associated with the cochlear aqueduct.
Vestibular aqueduct|canals|temporal-bone|35,-11,-26|superior|1|Bony channel opening on the posterior petrous surface behind the internal acoustic meatus. This is an approximate location pin.
Stylomastoid foramen|foramina|temporal-bone|45,-42,-14|inferior|1|Inferior temporal opening between the styloid and mastoid processes, marking the external end of the facial canal.
Mastoid foramen|foramina|temporal-bone|49,-16,-45|posterior|1|Variable emissary opening near the posterior mastoid region, sometimes crossing the occipitomastoid suture.
Jugular foramen|foramina|occipital-bone,temporal-bone|29,-37,-15|inferior|1|Irregular opening between the petrous temporal bone and occipital jugular notch, lateral to the hypoglossal canal.
Foramen lacerum|foramina|sphenoid-bone,temporal-bone,occipital-bone|15,-25,2|inferior|1|Irregular opening at the junction of the sphenoid, petrous temporal apex and basilar occipital bone; much of it is cartilage-filled in life.
Hiatus for greater petrosal nerve|foramina|temporal-bone|31,-6,-4|superior|1|Small opening on the anterior petrous temporal surface leading into a shallow groove.
Hiatus for lesser petrosal nerve|foramina|temporal-bone|36,-7,-1|superior|1|Small opening lateral to the greater petrosal hiatus on the anterior petrous temporal surface.
Groove for greater petrosal nerve|grooves|temporal-bone|25,-10,1|superior|1|Shallow anteromedial groove from the greater petrosal hiatus on the petrous temporal surface.
Groove for lesser petrosal nerve|grooves|temporal-bone|34,-9,3|superior|1|Shallow groove running anteriorly from the lesser petrosal hiatus.
Groove for superior petrosal sinus|grooves|temporal-bone|27,-1,-14|superior|1|Vascular groove along the superior border of the petrous temporal bone.
Groove for inferior petrosal sinus|grooves|temporal-bone,occipital-bone|20,-29,-14|superior|1|Vascular impression along the petro-occipital junction toward the jugular foramen.
Groove for sigmoid sinus|grooves|temporal-bone,occipital-bone|45,-14,-37|superior|1|Curved vascular groove on the internal mastoid and adjacent occipital surface leading toward the jugular foramen.
Groove for middle temporal artery|grooves|temporal-bone|62,5,1|left|1|Shallow vascular impression on the external squamous temporal surface near the root of its zygomatic process.
Supramastoid crest|ridges|temporal-bone|60,-2,-7|left|1|Ridge extending posteriorly from the root of the zygomatic process above the external acoustic meatus.
Suprameatal triangle|fossae|temporal-bone|60,-12,-14|left|1|Small external temporal depression just posterosuperior to the external acoustic opening.
Suprameatal spine|processes|temporal-bone|62,-17,-7|left|1|Small projection at the posterosuperior edge of the external acoustic opening; its prominence varies.
Parietal notch of temporal bone|notches|temporal-bone|55,2,-40|left|1|Indentation between the temporal squamous and mastoid margins that receives the mastoid angle of the parietal bone.
Mastoid part of temporal bone|parts|temporal-bone|53,-22,-32|posterior|1|Posterior temporal region behind the acoustic meatus, containing mastoid air cells and bearing the mastoid process.
Zygomatic process of temporal bone|processes|temporal-bone|58,-19,25|left|1|Anterior temporal projection forming the posterior and larger part of the zygomatic arch.
Articular tubercle of temporal bone|articulations|temporal-bone|48,-23,20|inferior|1|Rounded anterior boundary of the mandibular fossa along which the mandibular condyle moves.
Mandibular fossa|articulations|temporal-bone|45,-27,8|inferior|1|Temporal depression beneath the root of the zygomatic process that receives the mandibular condyle.
Postglenoid tubercle|processes|temporal-bone|52,-28,0|inferior|1|Small projection at the posterior margin of the temporal mandibular fossa.
Petrotympanic fissure|fissures|temporal-bone|44,-29,1|inferior|1|Narrow cleft near the posterior mandibular fossa between the tympanic region and petrous temporal bone.
Petrosquamous fissure|fissures|temporal-bone|43,-24,5|superior|1|Junction between the petrous and squamous temporal regions near the roof of the tympanic cavity.
Tympanomastoid fissure|fissures|temporal-bone|49,-34,-13|inferior|1|Cleft between the tympanic and mastoid parts of the temporal bone.
Tympanosquamous fissure|fissures|temporal-bone|54,-24,5|inferior|1|Cleft separating the tympanic plate from the squamous temporal bone near the mandibular fossa.
Tympanic ring|ridges|temporal-bone|56,-24,-2|left|1|Curved tympanic bony margin surrounding the deep end of the external acoustic meatus.
Tympanic sulcus|grooves|temporal-bone|52,-23,-2|left|1|Groove on the inner tympanic ring for attachment of the tympanic membrane.
Tympanic notch|notches|temporal-bone|54,-17,-2|left|1|Superior interruption in the bony tympanic ring.
Tympanic cavity|parts|temporal-bone|46,-19,-4|left|1|Air-filled bony middle-ear chamber containing the auditory ossicles. This marker locates the chamber, without replacing its walls.
Vestibular window|foramina|temporal-bone|41,-20,-3|left|1|Oval opening in the medial wall of the bony tympanic cavity, occupied by the stapes footplate in life.
Cochlear window|foramina|temporal-bone|40,-23,-5|left|1|Round opening inferior to the vestibular window in the medial tympanic wall.
Promontory of tympanic cavity|processes|temporal-bone|41,-22,-1|left|1|Rounded bony prominence on the medial tympanic wall over the basal turn of the cochlea.
Pyramidal eminence|processes|temporal-bone|43,-22,-7|left|1|Small posterior tympanic-wall projection associated with the stapedius canal.
Tegmen mastoideum|parts|temporal-bone|47,-10,-23|superior|1|Thin bony roof over the mastoid antrum, continuous with the tegmen tympani.
Trigeminal impression|fossae|temporal-bone|19,-12,0|superior|1|Shallow depression near the apex of the anterior petrous surface for the trigeminal ganglion.
Superior orbital fissure|fissures|sphenoid-bone|25,0,47|anterior|1|Cleft between the greater and lesser sphenoidal wings at the back of the orbit.
Inferior orbital fissure|fissures|sphenoid-bone,maxilla,zygomatic-bone,palatine-bone|31,-20,48|anterior|1|Gap along the posterior orbital floor between the maxilla and greater sphenoidal wing, with lateral and medial bony contributions.
Pterygomaxillary fissure|fissures|sphenoid-bone,maxilla|27,-34,40|left|1|Vertical gap between the posterior maxilla and sphenoid pterygoid process, opening medially toward the pterygopalatine fossa.
Sphenopetrosal fissure|fissures|sphenoid-bone,temporal-bone|24,-21,0|superior|1|Narrow cranial-base junction between the sphenoid and petrous temporal bone.
Petro-occipital fissure|fissures|temporal-bone,occipital-bone|23,-29,-16|superior|1|Junction between the petrous temporal bone and basilar occipital bone beside the clivus.
Sphenoidal rostrum|ridges|sphenoid-bone|0,-26,30|inferior|0|Midline inferior ridge of the sphenoid body that fits between the wings of the vomer.
Infratemporal crest|ridges|sphenoid-bone|46,-17,22|left|1|Ridge separating the temporal and infratemporal surfaces of the greater sphenoidal wing.
Sphenoidal concha|parts|sphenoid-bone|7,-19,43|anterior|1|Thin curved bony plate completing the anteroinferior wall of the sphenoidal sinus.
Sphenoidal sinus opening|foramina|sphenoid-bone|6,-6,42|anterior|1|Opening in the anterior sphenoid body into the sphenoethmoidal recess.
Palatovaginal canal|canals|sphenoid-bone,palatine-bone|9,-30,30|inferior|1|Small passage at the sphenoid vaginal process and sphenoidal process of the palatine bone.
Vomerovaginal canal|canals|sphenoid-bone,vomer|4,-30,29|inferior|1|Variable small passage between the sphenoid vaginal process and ala of the vomer.
Crista galli|processes|ethmoid-bone|0,3,54|superior|0|Midline upward ethmoid projection between the cribriform plates in the anterior cranial fossa.
Cribriform plate|parts|ethmoid-bone|5,-2,55|superior|1|Perforated horizontal ethmoid plate forming the nasal roof beside the crista galli.
Olfactory foramina|foramina|ethmoid-bone|5,-2,58|superior|1|Group of tiny openings through a cribriform plate. The marker represents the group, not separately segmented individual holes.
Perpendicular plate of ethmoid|parts|ethmoid-bone|0,-20,58|anterior|0|Vertical ethmoid plate forming the superior portion of the bony nasal septum.
Ethmoidal labyrinth|parts|ethmoid-bone|13,-15,51|anterior|1|Lateral ethmoid mass containing the ethmoidal air cells between the orbit and nasal cavity.
Orbital plate of ethmoid|parts|ethmoid-bone|17,-12,52|anterior|1|Thin lateral ethmoidal plate forming much of the medial orbital wall; also called the lamina papyracea.
Superior nasal concha|parts|ethmoid-bone|8,-12,45|anterior|1|Small curved ethmoid projection into the upper lateral nasal cavity.
Middle nasal concha|parts|ethmoid-bone|10,-24,59|anterior|1|Curved ethmoid projection below the superior concha; it belongs to the ethmoid rather than being an independent bone.
Uncinate process of ethmoid|processes|ethmoid-bone|13,-26,61|anterior|1|Thin curved ethmoidal plate on the lateral nasal wall, anterior to the ethmoidal bulla.
Ethmoidal bulla|processes|ethmoid-bone|13,-20,53|anterior|1|Rounded prominence of middle ethmoidal air cells on the lateral wall of the middle nasal meatus.
Ethmoidal infundibulum|canals|ethmoid-bone,maxilla|14,-27,59|anterior|1|Narrow bony-walled channel beside the uncinate process on the lateral nasal wall.
Hiatus semilunaris|fissures|ethmoid-bone|13,-25,56|anterior|1|Crescent-shaped gap between the ethmoidal bulla and uncinate process in the middle nasal meatus.
Anterior ethmoidal foramen|foramina|ethmoid-bone,frontal-bone|15,-2,61|anterior|1|Opening along the frontoethmoidal junction in the medial orbital roof, anterior to the posterior ethmoidal foramen.
Posterior ethmoidal foramen|foramina|ethmoid-bone,frontal-bone|15,-1,44|anterior|1|Posterior opening at the frontoethmoidal junction in the medial orbital wall near the orbital apex.
Supraorbital foramen|foramina|frontal-bone|25,1,78|anterior|1|Alternative enclosed form of the supraorbital passage in the superior orbital rim; a notch and foramen are anatomical variants.
Frontal notch|notches|frontal-bone|14,0,79|anterior|1|Medial notch of the frontal orbital rim for the supratrochlear neurovascular bundle; it may instead be a foramen.
Groove for superior sagittal sinus|grooves|frontal-bone,parietal-bone-left,parietal-bone-right,occipital-bone|0,91,-15|superior|0|Midline endocranial groove along the cranial vault, continuing from the frontal crest toward the internal occipital protuberance.
Granular foveolae|fossae|parietal-bone|8,90,-17|superior|1|Small variable pits near the sagittal border of the internal parietal surface, associated with arachnoid granulations.
Impressions of cerebral gyri|grooves|frontal-bone|25,34,63|superior|1|Shallow endocranial impressions of the brain surface; their number and depth vary between skulls.
Infraorbital foramen|foramina|maxilla|26,-26,78|anterior|1|Anterior opening of the infraorbital canal on the maxilla below the orbital rim.
Infraorbital canal|canals|maxilla|28,-21,66|anterior|1|Passage through the orbital floor of the maxilla, continuing from the infraorbital groove to its facial opening.
Infraorbital groove|grooves|maxilla|29,-19,54|superior|1|Groove in the posterior maxillary orbital floor that continues forward as the infraorbital canal.
Anterior nasal spine|processes|maxilla-left,maxilla-right|0,-48,91|anterior|0|Midline anterior projection at the lower border of the piriform aperture, formed by the two maxillae.
Frontal process of maxilla|processes|maxilla|11,-6,80|anterior|1|Upward maxillary projection beside the nasal bone, contributing to the medial orbital rim.
Zygomatic process of maxilla|processes|maxilla|39,-30,66|anterior|1|Lateral maxillary projection that articulates with the zygomatic bone.
Palatine process of maxilla|parts|maxilla|14,-56,68|inferior|1|Horizontal maxillary plate forming the anterior three quarters of the hard palate.
Alveolar process of maxilla|parts|maxilla|22,-59,73|anterior|1|Inferior tooth-bearing maxillary process containing the upper dental alveoli.
Maxillary tuberosity|processes|maxilla|27,-50,44|inferior|1|Rounded posterior maxillary prominence behind the last upper molar socket.
Canine eminence of maxilla|processes|maxilla|19,-43,85|anterior|1|Anterior maxillary bulge over the root of the upper canine tooth.
Canine fossa|fossae|maxilla|29,-35,77|anterior|1|Anterior maxillary depression lateral to the canine eminence and below the infraorbital foramen.
Incisive fossa of maxilla|fossae|maxilla|9,-42,91|anterior|1|Shallow anterior maxillary depression above the upper incisor sockets.
Incisive foramen|foramina|maxilla-left,maxilla-right|0,-56,80|inferior|0|Anterior midline hard-palate opening behind the upper incisors, communicating with the incisive canal.
Incisive canal|canals|maxilla-left,maxilla-right|0,-50,79|inferior|0|Passage through the anterior maxillary hard palate connecting the nasal floor and incisive fossa.
Alveolar foramina of maxilla|foramina|maxilla|31,-42,44|posterior|1|Small openings on the infratemporal maxillary surface for posterior superior alveolar branches.
Alveolar canals of maxilla|canals|maxilla|30,-43,48|posterior|1|Small passages from the posterior maxillary alveolar openings toward the tooth sockets; individual channels are not segmented.
Nasolacrimal canal|canals|maxilla,lacrimal-bone,inferior-nasal-concha|13,-27,74|anterior|1|Bony passage descending from the lacrimal-sac fossa to the inferior nasal meatus.
Lacrimal groove of maxilla|grooves|maxilla|13,-13,75|anterior|1|Groove on the medial maxillary frontal process that contributes to the lacrimal-sac fossa.
Anterior lacrimal crest|ridges|maxilla|14,-13,78|anterior|1|Ridge on the frontal maxillary process forming the anterior border of the lacrimal-sac fossa.
Ethmoidal crest of maxilla|ridges|maxilla|12,-20,69|anterior|1|Ridge on the nasal surface of the frontal maxillary process articulating with the middle nasal concha.
Conchal crest of maxilla|ridges|maxilla|13,-35,68|anterior|1|Ridge on the nasal maxillary surface that articulates with the inferior nasal concha.
Nasal notch of maxilla|notches|maxilla|11,-31,86|anterior|1|Curved medial maxillary border forming the lateral margin of the anterior nasal aperture.
Maxillary hiatus|foramina|maxilla|16,-35,55|anterior|1|Large opening in the medial wall of the isolated maxilla, narrowed by neighboring bones in the articulated skull.
Orbital surface of maxilla|parts|maxilla|30,-19,63|superior|1|Superior maxillary surface forming most of the orbital floor.
Nasal surface of maxilla|parts|maxilla|12,-40,61|anterior|1|Medial maxillary wall facing the nasal cavity, including the maxillary hiatus and conchal crest.
Infratemporal surface of maxilla|parts|maxilla|28,-38,43|posterior|1|Posterior maxillary surface facing the infratemporal and pterygopalatine regions.
Anterior surface of maxilla|parts|maxilla|27,-37,81|anterior|1|Facial maxillary surface below the orbit bearing the infraorbital foramen and canine fossa.
Frontal process of zygomatic bone|processes|zygomatic-bone|42,-1,58|anterior|1|Superior cheekbone projection articulating with the frontal bone at the lateral orbital rim.
Temporal process of zygomatic bone|processes|zygomatic-bone|57,-25,41|left|1|Posterior cheekbone projection meeting the temporal zygomatic process to complete the zygomatic arch.
Maxillary process of zygomatic bone|processes|zygomatic-bone|36,-30,65|anterior|1|Anteromedial zygomatic projection articulating with the zygomatic process of the maxilla.
Zygomaticofacial foramen|foramina|zygomatic-bone|47,-23,66|anterior|1|Small variable opening on the facial cheekbone surface.
Zygomaticotemporal foramen|foramina|zygomatic-bone|49,-12,46|left|1|Small opening on the temporal surface of the zygomatic bone.
Zygomaticoorbital foramen|foramina|zygomatic-bone|40,-15,56|anterior|1|Small opening on the orbital surface of the zygomatic bone leading into its internal canal system.
Orbital surface of zygomatic bone|parts|zygomatic-bone|39,-18,62|anterior|1|Smooth cheekbone surface forming the anterolateral orbital wall and a portion of the orbital floor.
Temporal surface of zygomatic bone|parts|zygomatic-bone|49,-23,43|left|1|Posteromedial zygomatic surface contributing to the temporal and infratemporal boundaries.
Lateral surface of zygomatic bone|parts|zygomatic-bone|51,-27,66|anterior|1|Convex external cheekbone surface bearing the zygomaticofacial opening.
Zygomatic arch|parts|zygomatic-bone,temporal-bone|58,-24,33|left|1|Bony bridge formed by the temporal process of the zygomatic bone and zygomatic process of the temporal bone.
Posterior lacrimal crest|ridges|lacrimal-bone|13,-13,71|anterior|1|Vertical lacrimal ridge forming the posterior border of the lacrimal-sac fossa.
Lacrimal groove of lacrimal bone|grooves|lacrimal-bone|11,-15,74|anterior|1|Anterior lacrimal groove contributing to the fossa for the lacrimal sac.
Lacrimal hamulus|processes|lacrimal-bone|12,-23,74|anterior|1|Hook-like inferior end of the posterior lacrimal crest near the entrance to the nasolacrimal canal.
Fossa for lacrimal sac|fossae|lacrimal-bone,maxilla|12,-15,75|anterior|1|Bony depression between the anterior and posterior lacrimal crests at the medial orbital rim.
Horizontal plate of palatine bone|parts|palatine-bone|11,-53,43|inferior|1|Horizontal palatine plate forming the posterior part of the hard palate.
Perpendicular plate of palatine bone|parts|palatine-bone|17,-34,42|anterior|1|Vertical palatine plate contributing to the posterior lateral nasal wall.
Orbital process of palatine bone|processes|palatine-bone|23,-22,44|anterior|1|Small superior palatine process contributing a tiny area to the posterior orbital floor.
Sphenoidal process of palatine bone|processes|palatine-bone|12,-26,31|inferior|1|Posterior superior palatine projection articulating with the sphenoid.
Pyramidal process of palatine bone|processes|palatine-bone|20,-49,32|inferior|1|Posteroinferior palatine projection fitting between the sphenoid pterygoid plates.
Greater palatine foramen|foramina|palatine-bone|18,-54,45|inferior|1|Opening on the posterolateral hard palate at the lower end of the greater palatine canal.
Lesser palatine foramina|foramina|palatine-bone|19,-54,37|inferior|1|Small openings behind the greater palatine foramen in the palatine pyramidal process; the pin represents the group.
Greater palatine canal|canals|palatine-bone,maxilla|22,-39,43|posterior|1|Descending bony passage between maxilla and palatine bone from the pterygopalatine fossa to the hard palate.
Sphenopalatine notch|notches|palatine-bone|16,-24,35|anterior|1|Notch between the orbital and sphenoidal palatine processes, completed by the sphenoid as the sphenopalatine foramen.
Sphenopalatine foramen|foramina|palatine-bone,sphenoid-bone|16,-23,36|anterior|1|Opening between the sphenoid and superior palatine notch linking the pterygopalatine fossa to the nasal cavity.
Posterior nasal spine|processes|palatine-bone-left,palatine-bone-right|0,-52,29|inferior|0|Midline posterior projection at the junction of the horizontal palatine plates.
Nasal crest of palatine bone|ridges|palatine-bone|2,-49,43|superior|1|Medial superior ridge on a horizontal palatine plate supporting the vomer.
Ethmoidal crest of palatine bone|ridges|palatine-bone|15,-31,42|anterior|1|Upper transverse ridge on the nasal palatine surface articulating with the middle nasal concha.
Conchal crest of palatine bone|ridges|palatine-bone|14,-39,43|anterior|1|Lower transverse ridge on the nasal palatine surface articulating with the inferior nasal concha.
Greater palatine groove|grooves|palatine-bone|21,-38,46|posterior|1|Vertical groove on the maxillary surface of the palatine plate contributing to the greater palatine canal.
Lacrimal process of inferior nasal concha|processes|inferior-nasal-concha|12,-33,72|anterior|1|Small superior conchal projection articulating with the lacrimal bone near the nasolacrimal canal.
Maxillary process of inferior nasal concha|processes|inferior-nasal-concha|12,-39,60|anterior|1|Downward and lateral conchal projection that helps narrow the maxillary hiatus.
Ethmoidal process of inferior nasal concha|processes|inferior-nasal-concha|12,-29,57|anterior|1|Superior conchal projection connecting toward the ethmoidal uncinate process.
Ala of vomer|parts|vomer|4,-23,30|inferior|1|Flared superior vomer wing fitting against the inferior sphenoid body beside its rostrum.
Groove for nasopalatine nerve|grooves|vomer|2,-32,55|left|1|Oblique groove on a vomer surface running toward the anterior hard palate.
Posterior border of vomer|parts|vomer|0,-32,25|posterior|0|Free posterior vomer edge separating the two posterior nasal apertures.
Superior border of vomer|parts|vomer|0,-23,33|superior|0|Grooved upper vomer border receiving the sphenoidal rostrum between its alae.
Inferior border of vomer|parts|vomer|0,-41,51|inferior|0|Lower vomer edge articulating with the nasal crests of the maxillae and palatine bones.
Anterior border of vomer|parts|vomer|0,-32,76|anterior|0|Anterior vomer edge joining the perpendicular ethmoid plate above and septal cartilage below in life.
Groove for anterior ethmoidal nerve|grooves|nasal-bone|4,-12,88|posterior|1|Small longitudinal groove on the internal nasal-bone surface.
Nasal foramen|foramina|nasal-bone|4,-9,92|anterior|1|Small variable vascular opening on the nasal bone.
Ramus of mandible|parts|mandible|43,-53,18|left|1|Upright posterior mandibular segment between the angle and the coronoid and condylar processes.
Angle of mandible|landmarks|mandible|40,-76,12|left|1|Posteroinferior corner where the mandibular ramus meets its body; the craniometric region is gonion.
Condylar process of mandible|processes|mandible|43,-30,7|left|1|Posterior superior mandibular projection formed by the condylar head and neck.
Head of mandible|articulations|mandible|44,-26,8|inferior|1|Articular head of the condylar process that meets the temporal mandibular fossa through the joint disc.
Neck of mandible|parts|mandible|43,-38,9|left|1|Narrow segment below the mandibular head, bearing the anterior pterygoid fovea.
Coronoid process of mandible|processes|mandible|39,-34,35|left|1|Anterior superior projection of the mandibular ramus, providing the temporalis insertion.
Mandibular notch|notches|mandible|42,-38,23|left|1|Curved superior ramus gap between the coronoid and condylar processes.
Mandibular foramen|foramina|mandible|36,-50,21|posterior|1|Opening on the medial ramus leading into the mandibular canal.
Mandibular canal|canals|mandible|31,-72,45|left|1|Internal passage through the mandibular ramus and body from the mandibular foramen toward the mental foramen. Its lumen is not separately segmented.
Lingula of mandible|processes|mandible|35,-47,23|posterior|1|Small tongue-shaped projection bordering the mandibular foramen anteriorly.
Mylohyoid groove|grooves|mandible|34,-59,25|posterior|1|Groove descending forward from the medial mandibular foramen below the mylohyoid line.
Pterygoid fovea of mandible|fossae|mandible|39,-38,13|posterior|1|Depression on the anterior mandibular neck for the lateral pterygoid attachment.
Submandibular fossa|fossae|mandible|25,-81,52|posterior|1|Depression on the inner mandibular body below the posterior mylohyoid line.
Alveolar part of mandible|parts|mandible|23,-71,61|superior|1|Tooth-bearing superior mandibular body containing the lower dental sockets.
Dental alveoli of mandible|parts|mandible|24,-72,61|superior|1|Bony sockets within the mandibular alveolar part; this marker represents a region rather than each socket.
Dental alveoli of maxilla|parts|maxilla|24,-57,66|inferior|1|Bony sockets within the maxillary alveolar process; individual tooth sockets are not separate meshes.
Retromolar triangle|fossae|mandible|31,-64,35|superior|1|Small area behind the last mandibular molar socket between the ramus borders.
Masseteric tuberosity|ridges|mandible|43,-65,18|left|1|Roughened lateral surface near the mandibular angle for the masseter insertion.
Pterygoid tuberosity|ridges|mandible|35,-69,17|posterior|1|Roughened medial mandibular angle for the medial pterygoid insertion.
Short limb of incus|parts|incus|47,-17,-7|anterior|1|Short posterior incus limb projecting toward the posterior wall of the tympanic cavity.
Obturator foramen of stapes|foramina|stapes|43,-20,-3|anterior|1|Opening between the anterior and posterior stapes crura above the footplate.
Clivus|parts|sphenoid-bone,occipital-bone|0,-23,-3|superior|0|Sloping central cranial-base surface from the dorsum sellae toward the anterior margin of the foramen magnum.
Nuchal plane|parts|occipital-bone|0,-24,-75|posterior|0|External occipital surface below the superior nuchal lines, providing neck-muscle attachments.
Basion|landmarks|occipital-bone|0,-42,-8|inferior|0|Midline point on the anterior margin of the foramen magnum.
Opisthion|landmarks|occipital-bone|0,-36,-43|inferior|0|Midline point on the posterior margin of the foramen magnum.
Inion|landmarks|occipital-bone|0,-5,-100|posterior|0|Most prominent midline point of the external occipital protuberance.
Nasion|landmarks|frontal-bone,nasal-bone-left,nasal-bone-right|0,1,84|anterior|0|Midline junction of the frontal bone and paired nasal bones at the root of the nose.
Bregma|landmarks|frontal-bone,parietal-bone-left,parietal-bone-right|0,98,22|superior|0|Midline intersection of the coronal and sagittal sutures in the adult skull.
Lambda|landmarks|parietal-bone-left,parietal-bone-right,occipital-bone|0,53,-96|posterior|0|Midline intersection of the sagittal and lambdoid sutures.
Pterion|landmarks|frontal-bone,parietal-bone,temporal-bone,sphenoid-bone|56,15,35|left|1|Lateral junction of the frontal, parietal, squamous temporal and greater-wing sphenoidal bones.
Asterion|landmarks|parietal-bone,temporal-bone,occipital-bone|56,-5,-49|posterior|1|Junction of parietal, mastoid temporal and occipital bones at the posterior side of the skull.
Porion|landmarks|temporal-bone|61,-17,-4|left|1|Reference point at the superior margin of the bony external acoustic opening.
Orbitale|landmarks|maxilla,zygomatic-bone|31,-23,67|anterior|1|Reference point on the lowest margin of the bony orbit.
Vertex|landmarks|parietal-bone-left,parietal-bone-right|0,104,-25|superior|0|Highest point of the cranial vault in the anatomical head position.
Prosthion|landmarks|maxilla-left,maxilla-right|0,-61,93|anterior|0|Anterior midline point of the upper alveolar process between the central incisor sockets.
Pogonion|landmarks|mandible|0,-95,84|anterior|0|Most anterior midline point on the bony mandibular chin.
Menton|landmarks|mandible|0,-103,75|anterior|0|Lowest midline point of the inferior mandibular symphysis.
Temporal fossa|fossae|frontal-bone,parietal-bone,temporal-bone,sphenoid-bone|66,21,14|left|1|Broad lateral skull depression above the zygomatic arch bounded superiorly by the temporal lines.
Infratemporal fossa|fossae|sphenoid-bone,maxilla,temporal-bone,mandible|38,-34,26|left|1|Region deep to the mandibular ramus and below the greater sphenoidal wing, behind the maxilla.
Pterygopalatine fossa|fossae|sphenoid-bone,maxilla,palatine-bone|23,-31,39|left|1|Small space between the posterior maxilla, sphenoid pterygoid process and perpendicular palatine plate.
Anterior cranial fossa|fossae|frontal-bone,ethmoid-bone,sphenoid-bone|21,1,61|superior|1|Most anterior cranial-floor depression, formed mainly by the frontal orbital plates around the cribriform ethmoid region.
Middle cranial fossa|fossae|sphenoid-bone,temporal-bone|34,-8,17|superior|1|Cranial-floor depression lateral to the sella turcica, behind the lesser sphenoidal wing.
Posterior cranial fossa|fossae|occipital-bone,temporal-bone|25,-19,-48|superior|1|Deep posterior cranial-floor depression behind the petrous temporal ridges and clivus, around the foramen magnum.
Piriform aperture|foramina|maxilla-left,maxilla-right,nasal-bone-left,nasal-bone-right|0,-29,91|anterior|0|Pear-shaped anterior bony nasal opening bounded by the nasal bones and maxillae.
Choana|foramina|vomer,palatine-bone,sphenoid-bone|10,-37,29|posterior|1|Posterior bony nasal opening beside the vomer, bounded by palatine and sphenoidal contributions.
Superior nasal meatus|parts|ethmoid-bone|10,-16,46|anterior|1|Bony nasal space beneath the superior ethmoidal concha.
Middle nasal meatus|parts|ethmoid-bone,maxilla|12,-30,56|anterior|1|Bony nasal space beneath the middle ethmoidal concha, containing the semilunar hiatus region.
Inferior nasal meatus|parts|inferior-nasal-concha,maxilla|10,-45,61|anterior|1|Bony nasal space below the inferior nasal concha and above the hard palate.
Sphenoethmoidal recess|parts|sphenoid-bone,ethmoid-bone|6,-7,39|anterior|1|Space above and behind the superior nasal concha, adjacent to the sphenoidal sinus opening.
Hard palate|parts|maxilla-left,maxilla-right,palatine-bone-left,palatine-bone-right|0,-55,59|inferior|0|Bony oral roof formed anteriorly by the maxillary palatine processes and posteriorly by the horizontal palatine plates.
Bony nasal septum|parts|ethmoid-bone,vomer|0,-28,60|anterior|0|Midline bony nasal partition formed chiefly by the perpendicular ethmoid plate above and vomer below and behind.
Maxillary sinus|sinuses|maxilla|24,-38,64|anterior|1|Air-filled bony cavity within the maxillary body below the orbital floor. The study pin locates the cavity; its complete wall is not a separate mesh here.
Mastoid air cells|sinuses|temporal-bone|51,-23,-29|posterior|1|Variable interconnected air spaces in the mastoid temporal region; individual cell walls are not separately segmented.
Mastoid antrum|sinuses|temporal-bone|48,-15,-20|superior|1|Larger mastoid air chamber posterior to the epitympanic region of the middle ear.
Bony vestibule|parts|temporal-bone|36,-19,-8|superior|1|Central chamber of the bony inner-ear labyrinth between the cochlea and semicircular canals. This study pin does not reconstruct the internal cavity.
Bony cochlea|parts|temporal-bone|34,-22,-2|superior|1|Spiraling bony inner-ear cavity anterior to the vestibule within the petrous temporal bone. Its internal turns are not separately segmented here.
Anterior semicircular canal|canals|temporal-bone|36,-11,-10|superior|1|Upper bony semicircular canal in the petrous temporal region, approximately above the vestibule. The pin identifies its region rather than an exported lumen.
Posterior semicircular canal|canals|temporal-bone|35,-18,-15|superior|1|Posterior bony semicircular canal behind the vestibule in the petrous temporal region. Its lumen is not separately segmented.
Lateral semicircular canal|canals|temporal-bone|40,-17,-10|superior|1|Near-horizontal bony semicircular canal lateral to the vestibule. Its internal course is represented by a study location only.
Orbital surface of palatine bone|parts|palatine-bone|23,-21,46|superior|1|Small superior surface of the palatine orbital process contributing to the posterior orbital floor.
Nasal surface of palatine bone|parts|palatine-bone|14,-36,43|anterior|1|Medial surface of the perpendicular palatine plate, bearing ethmoidal and conchal crests on the lateral nasal wall.
Maxillary surface of palatine bone|parts|palatine-bone|21,-37,45|posterior|1|Lateral perpendicular-plate surface articulating with the maxilla and bearing the greater palatine groove.
Palatine surface of palatine bone|parts|palatine-bone|12,-55,42|inferior|1|Inferior horizontal-plate surface facing the oral cavity in the posterior hard palate.
Nasal surface of lacrimal bone|parts|lacrimal-bone|9,-15,69|anterior|1|Medial lacrimal surface adjoining the ethmoid and lateral nasal region.
Orbital surface of lacrimal bone|parts|lacrimal-bone|14,-15,70|anterior|1|Lateral lacrimal surface in the medial orbital wall, divided by the posterior lacrimal crest.
External surface of nasal bone|parts|nasal-bone|4,-11,94|anterior|1|Convex anterior nasal-bone surface forming the bony bridge of the nose.
Internal surface of nasal bone|parts|nasal-bone|4,-11,87|posterior|1|Concave posterior nasal-bone surface facing the upper nasal cavity.
Superior border of nasal bone|parts|nasal-bone|4,1,83|anterior|1|Upper nasal-bone edge articulating with the frontal bone at the frontonasal suture.
Inferior border of nasal bone|parts|nasal-bone|5,-25,98|anterior|1|Lower nasal-bone edge bordering the upper part of the anterior nasal aperture and septal cartilage region.
Medial surface of inferior nasal concha|parts|inferior-nasal-concha|9,-37,60|anterior|1|Convex conchal surface projecting into the nasal cavity beneath the middle concha.
Lateral surface of inferior nasal concha|parts|inferior-nasal-concha|14,-37,59|anterior|1|Concave outer conchal surface bounding the inferior nasal meatus.
Inferior border of inferior nasal concha|parts|inferior-nasal-concha|10,-46,61|anterior|1|Free curled lower edge of the inferior nasal concha above the inferior nasal meatus.
Temporal crest of mandible|ridges|mandible|35,-43,32|posterior|1|Ridge on the medial mandibular coronoid and ramus region contributing to the borders of the retromolar area.
Interalveolar septa of maxilla|parts|maxilla|24,-56,62|inferior|1|Bony partitions between adjacent upper dental sockets. Individual partitions are not separate catalogue meshes.
Interalveolar septa of mandible|parts|mandible|24,-74,57|superior|1|Bony partitions between adjacent lower dental sockets, within the mandibular alveolar part.
Interradicular septa of maxilla|parts|maxilla|26,-52,59|inferior|1|Bony partitions between roots within a multirooted upper tooth socket. The marker represents the molar region rather than every partition.
Interradicular septa of mandible|parts|mandible|26,-77,55|superior|1|Bony partitions between roots within a multirooted lower tooth socket. Individual root partitions are not separately segmented.
"""


# Names, owners and sorting choices for actual reference-bone contact paths.
# No pre-existing suture curve is claimed: paths are derived by the exporter.
SUTURES = [
    ("Coronal suture", ["frontal-bone", "parietal-bone-left", "parietal-bone-right"], "superior", "Boundary between the frontal bone and both parietal bones across the cranial vault.", "coronal"),
    ("Sagittal suture", ["parietal-bone-left", "parietal-bone-right"], "superior", "Midline joint between the two parietal bones, from bregma to lambda.", "sagittal"),
    ("Lambdoid suture", ["occipital-bone", "parietal-bone-left", "parietal-bone-right"], "posterior", "Posterior vault boundary between the occipital bone and both parietal bones.", "lambdoid"),
    ("Squamous suture", ["parietal-bone", "temporal-bone"], "left", "Overlapping lateral skull joint between the inferior parietal edge and squamous temporal bone.", "paired"),
    ("Parietomastoid suture", ["parietal-bone", "temporal-bone"], "posterior", "Posteroinferior parietal joint with the mastoid temporal region, behind the squamous suture near asterion.", "paired"),
    ("Occipitomastoid suture", ["occipital-bone", "temporal-bone"], "posterior", "Posterolateral skull joint between the occipital bone and mastoid temporal region.", "paired"),
    ("Sphenofrontal suture", ["sphenoid-bone", "frontal-bone"], "anterior", "Joint between the sphenoid and frontal bone near the posterior orbital roof and lateral cranial wall.", "paired"),
    ("Sphenoethmoidal suture", ["sphenoid-bone", "ethmoid-bone"], "superior", "Midline cranial-base junction of the anterior sphenoid body and posterior ethmoid.", "single"),
    ("Sphenosquamous suture", ["sphenoid-bone", "temporal-bone"], "left", "Lateral skull junction of the greater sphenoidal wing and squamous temporal bone.", "paired"),
    ("Sphenoparietal suture", ["sphenoid-bone", "parietal-bone"], "left", "Short lateral skull joint between the greater sphenoidal wing and sphenoidal parietal angle near pterion.", "paired"),
    ("Frontonasal suture", ["frontal-bone", "nasal-bone"], "anterior", "Joint between the nasal bones and frontal bone at the nasal root.", "paired"),
    ("Frontoethmoidal suture", ["frontal-bone", "ethmoid-bone"], "superior", "Joint along the ethmoid and frontal orbital plate in the medial orbital roof.", "paired"),
    ("Frontomaxillary suture", ["frontal-bone", "maxilla"], "anterior", "Joint of the frontal bone and ascending frontal process of the maxilla at the medial orbital rim.", "paired"),
    ("Frontolacrimal suture", ["frontal-bone", "lacrimal-bone"], "anterior", "Superior lacrimal joint with the frontal bone on the medial orbital wall.", "paired"),
    ("Frontozygomatic suture", ["frontal-bone", "zygomatic-bone"], "anterior", "Joint of the frontal and zygomatic processes at the lateral orbital rim.", "paired"),
    ("Zygomaticomaxillary suture", ["zygomatic-bone", "maxilla"], "anterior", "Joint between the cheekbone and lateral maxilla, extending toward the inferior orbital rim.", "paired"),
    ("Ethmoidomaxillary suture", ["ethmoid-bone", "maxilla"], "anterior", "Joint between the ethmoid and maxilla on the medial orbital and lateral nasal walls.", "paired"),
    ("Ethmoidolacrimal suture", ["ethmoid-bone", "lacrimal-bone"], "anterior", "Joint between the posterior lacrimal bone and ethmoidal orbital plate.", "paired"),
    ("Sphenovomerine suture", ["sphenoid-bone", "vomer"], "inferior", "Midline joint of the sphenoidal rostrum with the alae of the vomer.", "single"),
    ("Sphenozygomatic suture", ["sphenoid-bone", "zygomatic-bone"], "anterior", "Joint between the greater sphenoidal wing and zygomatic bone in the lateral orbital wall.", "paired"),
    ("Temporozygomatic suture", ["temporal-bone", "zygomatic-bone"], "left", "Joint uniting the temporal and zygomatic processes in the zygomatic arch.", "paired"),
    ("Internasal suture", ["nasal-bone-left", "nasal-bone-right"], "anterior", "Midline joint between the two nasal bones on the bony nasal bridge.", "single"),
    ("Nasomaxillary suture", ["nasal-bone", "maxilla"], "anterior", "Joint between a nasal bone and the frontal process of the adjacent maxilla.", "paired"),
    ("Lacrimomaxillary suture", ["lacrimal-bone", "maxilla"], "anterior", "Joint of the lacrimal bone and maxilla in the anterior medial orbital wall.", "paired"),
    ("Lacrimoconchal suture", ["lacrimal-bone", "inferior-nasal-concha"], "anterior", "Junction of the lacrimal bone and lacrimal process of the inferior nasal concha near the nasolacrimal canal.", "paired"),
    ("Intermaxillary suture", ["maxilla-left", "maxilla-right"], "anterior", "Midline joint between the paired maxillae in the anterior upper jaw.", "single"),
    ("Palatoethmoidal suture", ["palatine-bone", "ethmoid-bone"], "anterior", "Joint between the palatine and ethmoid bones in the posterior medial orbit and lateral nasal wall.", "paired"),
    ("Palatomaxillary suture", ["palatine-bone", "maxilla"], "inferior", "Joint between the palatine plate and posterior maxilla, with palatal and lateral nasal-wall portions.", "paired"),
    ("Median palatine suture", ["maxilla-left", "maxilla-right", "palatine-bone-left", "palatine-bone-right"], "inferior", "Midline hard-palate joint between the two maxillary palatine processes and paired horizontal palatine plates.", "median-palatine"),
    ("Transverse palatine suture", ["maxilla-left", "maxilla-right", "palatine-bone-left", "palatine-bone-right"], "inferior", "Crosswise hard-palate junction between the posterior maxillary palatine processes and anterior horizontal palatine plates.", "transverse-palatine"),
]


def source_details() -> dict[str, dict]:
    records = {}
    for line in SOURCE_NOTES.strip().splitlines():
        name, category, paired, view, description = line.split("|", 4)
        records[name] = {"category": category, "paired": paired == "1", "view": view, "description": description}
    return records


def curated_details() -> list[dict]:
    records = []
    for line in CURATED.strip().splitlines():
        name, category, owners, point, view, paired, description = line.split("|", 6)
        records.append({"name": name, "category": category, "owners": owners.split(","), "point": [float(v) for v in point.split(",")], "view": view, "paired": paired == "1", "description": description})
    return records
