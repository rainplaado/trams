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

st.set_page_config(page_title="Field Path Optimizer", layout="wide")
st.title("🚜 Field Path Optimizer by Rain")
st.markdown("Upload a zipped **shapefile**. JD exports with folders inside ZIPs are supported.")

# === USER INPUT ===
machine_width = st.number_input("Machine width (m)", value=48)

# Combined heading input: number + slider
col_heading_1, col_heading_2 = st.columns([2, 1])
with col_heading_1:
    current_heading_input = st.number_input("Current heading (°)", min_value=0, max_value=359, value=0)
with col_heading_2:
    current_heading_slider = st.slider("Adjust heading", min_value=0, max_value=359, value=current_heading_input)

current_heading = current_heading_slider
angle_step = 0.5

# === UPLOAD SHAPEFILE ===
uploaded_file = st.file_uploader("Upload zipped shapefile (.zip)", type="zip")

if uploaded_file:
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(uploaded_file, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        # Recursively find all .shp files
        shp_files = []
        for root, dirs, files in os.walk(tmpdir):
            for file in files:
                if file.endswith(".shp"):
                    full_path = os.path.join(root, file)
                    shp_files.append(full_path)

        if not shp_files:
            st.error("No .shp files found in the zip.")
        else:
            selected_shp = None
            if len(shp_files) == 1:
                selected_shp = shp_files[0]
                st.success(f"Found shapefile: {os.path.basename(selected_shp)}")
            else:
                file_names = [os.path.relpath(f, tmpdir) for f in shp_files]
                choice = st.selectbox("Multiple shapefiles found. Select one:", file_names)
                selected_shp = os.path.join(tmpdir, choice)

            # === LOAD FIELD ===
            gdf_all = gpd.read_file(selected_shp)
            if gdf_all.crs is None or not gdf_all.crs.is_projected:
                gdf_all = gdf_all.to_crs(epsg=32750)

            # Filter only polygons
            polygon_gdf = gdf_all[gdf_all.geometry.type.isin(["Polygon", "MultiPolygon"])]
            if polygon_gdf.empty:
                st.error("No polygon features found in the selected shapefile.")
                st.stop()

            # Let user pick which field polygon to use
            polygon_gdf = polygon_gdf.reset_index(drop=True)
            field_index = 0
            if len(polygon_gdf) > 1:
                st.info(f"{len(polygon_gdf)} fields found in the shapefile.")
                field_index = st.selectbox("Select which field to use:", options=polygon_gdf.index, format_func=lambda i: f"Field {i+1}")

            gdf = polygon_gdf.iloc[[field_index]]
            field_geom = gdf.geometry.iloc[0].buffer(0)
            origin = field_geom.centroid

            # === OPTIMIZATION ===
            angles = np.arange(0, 180, angle_step)
            best_angle = None
            best_pass_count = float("inf")
            best_lines = []

            for angle in angles:
                rotated_field = rotate(field_geom, angle, origin=origin, use_radians=False)
                rminy, rmaxy = rotated_field.bounds[1], rotated_field.bounds[3]
                cx = origin.x

                lines = []
                y = rminy - 2 * machine_width
                while y <= rmaxy + 2 * machine_width:
                    line = LineString([(cx - 1e5, y), (cx + 1e5, y)])
                    lines.append(line)
                    y += machine_width

                clipped = [line.intersection(rotated_field) for line in lines if not line.intersection(rotated_field).is_empty]

                pass_count = 0
                for geom in clipped:
                    if geom.is_empty:
                        continue
                    elif isinstance(geom, LineString):
                        pass_count += 1
                    elif isinstance(geom, MultiLineString):
                        pass_count += len(geom.geoms)

                if pass_count < best_pass_count:
                    best_pass_count = pass_count
                    best_angle = angle
                    best_lines = clipped

            optimized_heading_forward = (best_angle - 90) % 360
            optimized_heading_reverse = (optimized_heading_forward + 180) % 360
            final_lines = [rotate(line, -best_angle, origin=origin, use_radians=False) for line in best_lines]

            # === CURRENT HEADING ===
            adjusted_current_heading = current_heading + 90
            rotated_current_field = rotate(field_geom, adjusted_current_heading, origin=origin, use_radians=False)
            cminy, cmaxy = rotated_current_field.bounds[1], rotated_current_field.bounds[3]
            current_lines = []
            cx = origin.x
            y = cminy - 2 * machine_width
            while y <= cmaxy + 2 * machine_width:
                line = LineString([(cx - 1e5, y), (cx + 1e5, y)])
                current_lines.append(line)
                y += machine_width

            clipped_current = [line.intersection(rotated_current_field)
                               for line in current_lines if not line.intersection(rotated_current_field).is_empty]

            current_passes = 0
            for geom in clipped_current:
                if geom.is_empty:
                    continue
                elif isinstance(geom, LineString):
                    current_passes += 1
                elif isinstance(geom, MultiLineString):
                    current_passes += len(geom.geoms)

            final_current_lines = [rotate(line, -adjusted_current_heading, origin=origin, use_radians=False)
                                   for line in clipped_current]

            def heading_label(deg):
                dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N']
                ix = int((deg % 360) / 45 + 0.5)
                return dirs[ix]

            # === STATS ===
            st.subheader("📊 Coverage Summary")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Optimized Heading (fwd)", f"{optimized_heading_forward:.1f}° ({heading_label(optimized_heading_forward)})")
                st.metric("Optimized Heading (rev)", f"{optimized_heading_reverse:.1f}° ({heading_label(optimized_heading_reverse)})")
                st.metric("Passes Needed", best_pass_count)
            with col2:
                st.metric("Current Heading", f"{current_heading}° ({heading_label(current_heading)})")
                st.metric("Passes Needed", current_passes)

            # === MAP ===
            st.subheader("Field Coverage Map (Google Base)")

            # Convert lines to GeoDataFrames
            optimized_gdf = gpd.GeoDataFrame(geometry=final_lines, crs=gdf.crs)
            current_gdf = gpd.GeoDataFrame(geometry=final_current_lines, crs=gdf.crs)

            # Reproject to lat/lon
            gdf_latlon = gdf.to_crs(epsg=4326)
            optimized_latlon = optimized_gdf.to_crs(epsg=4326)
            current_latlon = current_gdf.to_crs(epsg=4326)
            origin_latlon = gdf_latlon.geometry.iloc[0].centroid

            m = leafmap.Map(center=(origin_latlon.y, origin_latlon.x), zoom=17)
            m.add_basemap("HYBRID")
            m.add_gdf(gdf_latlon, layer_name="Field Boundary", style={"color": "green", "fillOpacity": 0.3})

            m.add_gdf(optimized_latlon, layer_name="Optimized Lines", style={"color": "blue", "weight": 2})
            m.add_gdf(current_latlon, layer_name="Current Lines", style={"color": "red", "weight": 1, "dashArray": "5,5"})

            # === LEGEND (only for tramlines) ===
            legend_html = """
            <div style="
                position: fixed;
                bottom: 30px;
                left: 30px;
                z-index: 1000;
                background-color: white;
                border: 2px solid gray;
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
                box-shadow: 2px 2px 8px rgba(0,0,0,0.3);
            ">
                <b>Legend</b><br>
                <span style="display:inline-block; width:12px; height:2px; background:blue; margin-right:6px;"></span>Optimized Lines<br>
                <span style="display:inline-block; width:12px; height:2px; background:red; border-bottom: 2px dashed red; margin-right:6px;"></span>Current Lines<br>
            </div>
            """

            components.html(m.to_html() + legend_html, height=650)
