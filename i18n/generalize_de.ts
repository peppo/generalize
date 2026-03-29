<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="de_DE" sourcelanguage="en">
<context>
    <name>_GeneralizeTask</name>
    <message>
        <source>Generalization cancelled.</source>
        <translation>Generalisierung abgebrochen.</translation>
    </message>
    <message>
        <source>Failed: </source>
        <translation>Fehlgeschlagen: </translation>
    </message>
    <message>
        <source>{count} feature(s) collapsed and were removed.</source>
        <translation>{count} Objekt(e) sind zu leerer Geometrie kollabiert und wurden entfernt.</translation>
    </message>
</context>
<context>
    <name>GeneralizeDialog</name>
    <message>
        <source>Generalize Polygons</source>
        <translation>Polygone generalisieren</translation>
    </message>
    <message>
        <source>Select Polygon Layer:</source>
        <translation>Polygonlayer auswählen:</translation>
    </message>
    <message>
        <source>Reduction Percentage:</source>
        <translation>Reduktionsgrad:</translation>
    </message>
    <message>
        <source>Repair geometry if necessary</source>
        <translation>Geometrie bei Bedarf reparieren</translation>
    </message>
    <message>
        <source>Dissolve small parts and holes</source>
        <translation>Kleine Teile und Löcher auflösen</translation>
    </message>
    <message>
        <source>Repair ring inversions</source>
        <translation>Ringinversionen reparieren</translation>
    </message>
    <message>
        <source>OK</source>
        <translation>OK</translation>
    </message>
    <message>
        <source>Cancel</source>
        <translation>Abbrechen</translation>
    </message>
    <message>
        <source>Error</source>
        <translation>Fehler</translation>
    </message>
    <message>
        <source>No layer selected.</source>
        <translation>Kein Layer ausgewählt.</translation>
    </message>
</context>
<context>
    <name>GeneralizePlugin</name>
    <message>
        <source>Generalize Polygons…</source>
        <translation>Polygone generalisieren…</translation>
    </message>
</context>
<context>
    <name>GeneralizeAlgorithm</name>
    <message>
        <source>Generalize polygons (topology-aware)</source>
        <translation>Polygone generalisieren</translation>
    </message>
    <message>
        <source>Simplifies polygon boundaries using the topology-aware Visvalingam algorithm. Shared edges between adjacent polygons are simplified exactly once, so no slivers or gaps are introduced between neighbours.

&lt;b&gt;Reduction percentage&lt;/b&gt;: how aggressively to simplify. 90 % removes 90 % of the vertices. Higher values produce coarser output; lower values stay closer to the original shape.

&lt;b&gt;Dissolve small parts and holes&lt;/b&gt;: after simplification, removes polygon parts and holes whose area falls below an automatic threshold (2 × average segment length²). Useful for cleaning up tiny slivers or island artefacts. At least one part per feature is always kept.

&lt;b&gt;Repair ring inversions&lt;/b&gt;: if aggressive simplification causes a ring to self-intersect (fold over itself), this option detects and corrects the inversion by restoring a small number of original vertices. Adds processing time on large datasets. It might be a good idea to ensure that the input layer is valid and does not contain overlapping polygons. After the generalization, the output layer should be checked for validity and repaired if necessary.</source>
        <translation>Vereinfacht Polygongrenzen mithilfe des topologiebewussten Visvalingam-Algorithmus. Gemeinsame Kanten zwischen benachbarten Polygonen werden genau einmal vereinfacht, sodass zwischen Nachbarn keine Splitter oder Lücken entstehen.

&lt;b&gt;Reduktionsgrad&lt;/b&gt;: Gibt an, wie stark vereinfacht wird. 90 % entfernen 90 % der Stützpunkte. Höhere Werte erzeugen gröbere Ergebnisse; niedrigere Werte bleiben näher an der ursprünglichen Form.

&lt;b&gt;Kleine Teile und Löcher auflösen&lt;/b&gt;: Entfernt nach der Vereinfachung Polygonteile und Löcher, deren Fläche unter einem automatisch berechneten Schwellenwert liegt (2 × mittlere Segmentlänge²). Nützlich zum Bereinigen kleiner Splitter oder Inselartefakte. Pro Objekt wird mindestens ein Teil behalten.

&lt;b&gt;Ringinversionen reparieren&lt;/b&gt;: Wenn eine aggressive Vereinfachung dazu führt, dass sich ein Ring selbst schneidet (überfaltet), erkennt und korrigiert diese Option die Inversion durch Wiederherstellung einer kleinen Anzahl ursprünglicher Stützpunkte. Erhöht die Verarbeitungszeit bei großen Datensätzen. Es empfiehlt sich sicherzustellen, dass der Eingabelayer gültig ist und keine überlappenden Polygone enthält. Nach der Generalisierung sollte der Ausgabelayer auf Gültigkeit geprüft und bei Bedarf repariert werden.</translation>
    </message>
    <message>
        <source>Input layer</source>
        <translation>Eingabelayer</translation>
    </message>
    <message>
        <source>Reduction percentage (%)</source>
        <translation>Reduktionsgrad (%)</translation>
    </message>
    <message>
        <source>Dissolve small parts and holes</source>
        <translation>Kleine Teile und Löcher auflösen</translation>
    </message>
    <message>
        <source>Repair ring inversions</source>
        <translation>Ringinversionen reparieren</translation>
    </message>
    <message>
        <source>Generalized</source>
        <translation>Generalisiert</translation>
    </message>
</context>
<context>
    <name>GeneralizeProvider</name>
    <message>
        <source>Generalize</source>
        <translation>Generalisieren</translation>
    </message>
    <message>
        <source>Topology-aware polygon generalisation</source>
        <translation>Topologiebewusste Polygongeneralisierung</translation>
    </message>
</context>
</TS>
