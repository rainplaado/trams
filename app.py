# SnailCam Visualizer v1.1.15 - Field Optimizer with Parallel Processing

import streamlit as st
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString
from shapely.affinity import rotate
import numpy as np
import tempfile
import zipfile
import os
import leafmap.foliumap as leafmap
import streamlit.components.v1 as components
import matplotlib.pyplot as plt
from fpdf import FPDF
import folium
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

st.set_page_config(page_title="Field Path Optimizer", layout="wide")
st.title("Field Path Optimizer by Rain Plaado")
st.markdown("Upload a zipped **shapefile** (boundaries) that you downloaded from JD opcentre (can include one or more fields).")

machine_width = st.number_input("Machine width (m)", value=48, step=1, format="%d")
angle_step = 0.5

uploaded_file = st.file_uploader("Upload zipped shapefile (.zip)", type="zip")

def optimize_field_for_parallel(args):
    row, i, origin, field_geom, shp, crs, machine_width, angle_step = args
    best_angle = None
    best_pass_count = float("inf")
    best_lines = []

    for angle in np.arange(0, 180, angle_step):
        rotated_field = rotate(field_geom, angle, origin=origin, use_radians=False)
        miny, maxy = rotated_field.bounds[1], rotated_field.bounds[3]
        y = miny - 2 * machine_width
        lines = []
        while y <= maxy + 2 * machine_width:
            lines.append(LineString([(origin.x - 1e5, y), (origin.x + 1e5, y)]))
            y += machine_width

        clipped_rotated = [line.intersection(rotated_field) for line in lines if not line.intersection(rotated_field).is_empty]
        rotated_back = [rotate(line, -angle, origin=origin, use_radians=False) for line in clipped_rotated]
        final_lines = [line.intersection(field_geom) for line in rotated_back if not line.intersection(field_geom).is_empty]

        count = 0
        for geom in final_lines:
            if geom.is_empty:
                continue
            elif isinstance(geom, LineString):
                count += 1
            elif isinstance(geom, MultiLineString):
                count += len(geom.geoms)
            else:
                continue

        if count < best_pass_count:
            best_pass_count = count
            best_angle = angle
            best_lines = final_lines

    forward = (best_angle - 90) % 360
    reverse = (forward + 180) % 360

    possible_name_fields = ['Name', 'Field', 'FIELD_NAME', 'ID', 'Label']
    field_name = None
    for col in possible_name_fields:
        if col in row and pd.notnull(row[col]):
            field_name = str(row[col])
            break
    if not field_name:
        field_name = f"{os.path.basename(shp)} - Field {i + 1}"

    return {
        "file": os.path.basename(shp),
        "field": i + 1,
        "name": field_name,
        "heading_fwd": forward,
        "heading_rev": reverse,
        "passes": best_pass_count,
        "geom": field_geom,
        "lines": best_lines,
        "crs": crs,
        "origin": origin
    }

if uploaded_file:
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(uploaded_file, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        shp_files = []
        for root, _, files in os.walk(tmpdir):
            for file in files:
                if file.endswith(".shp"):
                    shp_files.append(os.path.join(root, file))

        if not shp_files:
            st.error("No .shp files found in the zip.")
            st.stop()

        all_args = []
        for shp in shp_files:
            gdf_all = gpd.read_file(shp)
            if gdf_all.crs is None or not gdf_all.crs.is_projected:
                gdf_all = gdf_all.to_crs(epsg=32750)

            polygon_gdf = gdf_all[gdf_all.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)

            for i, row in polygon_gdf.iterrows():
                field_geom = row.geometry.buffer(0)
                origin = field_geom.centroid
                all_args.append((row, i, origin, field_geom, shp, gdf_all.crs, machine_width, angle_step))

        with st.spinner("Calculating optimal tramlines for all fields... This may take a few minutes."):
            with ProcessPoolExecutor() as executor:
                summary = list(executor.map(optimize_field_for_parallel, all_args))

        best_overall = min(summary, key=lambda x: x['passes'])

        st.subheader("\U0001F4CA Field Summary")
        for item in summary:
            st.markdown(f"**{item['name']}**: {item['passes']} passes @ {item['heading_fwd']:.1f}° forward")

        st.subheader("\U0001F30D Optimized Fields Map")
        center = summary[0]['geom'].centroid
        m = leafmap.Map(center=(center.y, center.x), zoom=17)
        m.add_basemap("HYBRID")

        for item in summary:
            field_gdf = gpd.GeoDataFrame(geometry=[item['geom']], crs=item['crs']).to_crs(epsg=4326)
            lines_gdf = gpd.GeoDataFrame(geometry=item['lines'], crs=item['crs']).to_crs(epsg=4326)
            m.add_gdf(field_gdf, style={"color": "green", "fillOpacity": 0.2})
            m.add_gdf(lines_gdf, style={"color": "blue", "weight": 2})
            label_point = field_gdf.geometry.iloc[0].centroid
            folium.Marker(
                location=[label_point.y, label_point.x],
                icon=folium.DivIcon(html=f"""
                    <div style="
                        font-size: 16px;
                        font-weight: bold;
                        color: black;
                        background-color: transparent;
                        padding: 0px;
                        border: none;
                        text-align: center;
                    ">
                        Best heading: {item['heading_fwd']:.1f}°
                    </div>
                """)
            ).add_to(m)

        components.html(m.to_html(), height=600)

        st.subheader("\U0001F4C4 Download PDF Report")
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        for item in summary:
            fig, ax = plt.subplots(figsize=(8, 8), dpi=300)  # Larger size + high DPI
            gpd.GeoSeries(item['geom']).boundary.plot(ax=ax, color='black')
            gpd.GeoSeries(item['lines']).plot(ax=ax, color='blue', linewidth=0.5)
            plt.axis('off')
            img_path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
            plt.savefig(img_path, bbox_inches='tight', dpi=300)  # High resolution

            plt.close(fig)

            pdf.add_page()
            pdf.set_font("Arial", size=12)
            pdf.cell(200, 10, txt=f"{item['name']}", ln=True, align="C")
            pdf.cell(200, 8, txt=f"Best heading: {item['heading_fwd']:.1f}°", ln=True)
            pdf.cell(200, 8, txt=f"Passes needed: {item['passes']}", ln=True)
            pdf.image(img_path, x=15, y=40, w=180)

        tmp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        pdf.output(tmp_pdf.name)
        with open(tmp_pdf.name, "rb") as f:
            st.download_button("Download PDF Summary", f.read(), file_name="field_optimization_report.pdf")
