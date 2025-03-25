import streamlit as st
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString
from shapely.affinity import rotate
import matplotlib.pyplot as plt
import numpy as np
import tempfile
import zipfile
import os

st.set_page_config(page_title="Field Path Optimizer", layout="wide")
st.title("🚜 Field Path Optimizer")
st.markdown("Upload a zipped **shapefile** of your field to compare optimal and current machine driving paths.")

# === UPLOAD SHAPEFILE ===
uploaded_file = st.file_uploader("Upload zipped shapefile (.zip)", type="zip")

# === USER INPUT ===
machine_width = st.number_input("Machine width (m)", value=48)
current_heading = st.slider("Current driving heading (°)", min_value=0, max_value=179, value=0)
machine_speed_kph = st.number_input("Machine speed (km/h)", value=20)
turn_time_sec = st.number_input("Turn time (seconds)", value=10)
angle_step = 0.5  # Optimization resolution

if uploaded_file:
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(uploaded_file, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        # Load shapefile
        shp_files = [f for f in os.listdir(tmpdir) if f.endswith(".shp")]
        if not shp_files:
            st.error("No .shp file found in the zip.")
        else:
            shapefile_path = os.path.join(tmpdir, shp_files[0])
            gdf = gpd.read_file(shapefile_path)
            if gdf.crs is None or not gdf.crs.is_projected:
                gdf = gdf.to_crs(epsg=32750)  # Adjust CRS as needed

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

            # Adjust for real-world heading
            adjusted_best_heading = (best_angle - 90) % 180
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

            def estimate_total_time(passes, width, geom, speed_kph, turn_sec):
                area_m2 = geom.area
                effective_speed_mps = speed_kph * 1000 / 3600
                total_distance = area_m2 / width
                driving_time = total_distance / effective_speed_mps
                turning_time = passes * turn_sec
                return driving_time + turning_time

            optimized_time = estimate_total_time(best_pass_count, machine_width, field_geom, machine_speed_kph, turn_time_sec)
            current_time = estimate_total_time(current_passes, machine_width, field_geom, machine_speed_kph, turn_time_sec)

            def heading_label(deg):
                dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N']
                ix = int((deg % 360) / 45 + 0.5)
                return dirs[ix]

            # === STATS ===
            st.subheader("📊 Coverage Summary")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Optimized Heading", f"{adjusted_best_heading:.1f}° ({heading_label(adjusted_best_heading)})")
                st.metric("Passes Needed", best_pass_count)
                st.metric("Estimated Time", f"{optimized_time / 60:.1f} min")
            with col2:
                st.metric("Current Heading", f"{current_heading}° ({heading_label(current_heading)})")
                st.metric("Passes Needed", current_passes)
                st.metric("Estimated Time", f"{current_time / 60:.1f} min")
                st.metric("⏱️ Time Saved", f"{(current_time - optimized_time) / 60:.1f} min")

            # === PLOT ===
            st.subheader("🗺️ Field Coverage Paths")
            fig, ax = plt.subplots(figsize=(10, 10))
            gdf.boundary.plot(ax=ax, color='black', linewidth=1)
            gdf.plot(ax=ax, color='lightgreen', alpha=0.5)

            for line in final_lines:
                if line.is_empty:
                    continue
                if isinstance(line, LineString):
                    x, y = line.xy
                    ax.plot(x, y, color='blue', linewidth=1, label='Optimized')
                elif isinstance(line, MultiLineString):
                    for part in line.geoms:
                        x, y = part.xy
                        ax.plot(x, y, color='blue', linewidth=1)

            for line in final_current_lines:
                if line.is_empty:
                    continue
                if isinstance(line, LineString):
                    x, y = line.xy
                    ax.plot(x, y, color='red', linewidth=1, linestyle='--', label='Current')
                elif isinstance(line, MultiLineString):
                    for part in line.geoms:
                        x, y = part.xy
                        ax.plot(x, y, color='red', linewidth=1, linestyle='--')

            handles, labels = ax.get_legend_handles_labels()
            unique = dict(zip(labels, handles))
            ax.legend(unique.values(), unique.keys())
            ax.set_title(f"Optimized: {adjusted_best_heading:.1f}° | Current: {current_heading:.1f}°")
            ax.axis('equal')
            plt.tight_layout()
            st.pyplot(fig)
