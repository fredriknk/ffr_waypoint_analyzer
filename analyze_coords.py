import tkinter as tk
from tkinter import filedialog
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import pandas as pd
import numpy as np
from matplotlib.patches import Rectangle
from sklearn.decomposition import PCA
from scipy.spatial import ConvexHull
import numpy as np
import matplotlib.pyplot as plt
from scipy.spatial import ConvexHull
import matplotlib.patches as patches
from math import cos, sin, sqrt
import json

def find_rectangles(points,best_angle):
    """
    For each point, find the distance to the nearest point in both x and y directions.
    Returns a list of distances for each point in the format: [distance_x, distance_y].
    """

    points = rotate_points(points, best_angle)

    rectangles = []

    for i, point in enumerate(points):
        min_dist =  float('inf')

        for j, other_point in enumerate(points):
            if i == j:
                continue  # Skip the same point comparison

            dist = sqrt((point[0] - other_point[0])**2 + (point[1] - other_point[1])**2)/2
            if dist < min_dist:
                min_dist = dist
        rect_corners = np.array([
            [point[0] - min_dist, point[1] - min_dist],
            [point[0] + min_dist, point[1] - min_dist],
            [point[0] + min_dist, point[1] + min_dist],
            [point[0] - min_dist, point[1] + min_dist]
        ])
        rectangles.append(rect_corners)

    rectangles = rotate_points(rectangles, -best_angle)
    return rectangles

def rotate_points(points, angle):
        """Rotate points by a given angle in radians."""
        rotation_matrix = np.array([[cos(angle), -sin(angle)], [sin(angle), cos(angle)]])
        return np.dot(points, rotation_matrix)

def get_bounding_box_area(points):
    """Calculate area of the bounding box for given points."""
    min_x, max_x = min(points[:, 0]), max(points[:, 0])
    min_y, max_y = min(points[:, 1]), max(points[:, 1])
    return (max_x - min_x) * (max_y - min_y)
def find_minimum_bounding_box(xy_points):

    hull = ConvexHull(xy_points)
    hull_points = xy_points[hull.vertices]

    min_area = float('inf')
    best_angle = 0
    trial_count = 0

    for edge in range(len(hull_points)):
        p1, p2 = hull_points[edge], hull_points[(edge + 1) % len(hull_points)]
        angle = np.arctan2(p2[1] - p1[1], p2[0] - p1[0])

        rotated_points = rotate_points(xy_points, angle)
        area = get_bounding_box_area(rotated_points)

        min_x, max_x = min(rotated_points[:, 0]), max(rotated_points[:, 0])
        min_y, max_y = min(rotated_points[:, 1]), max(rotated_points[:, 1])
        bounding_box = np.array([
            [min_x, min_y],
            [min_x, max_y],
            [max_x, max_y],
            [max_x, min_y],
            [min_x, min_y]  # Close the box
        ])
        if area < min_area:
            min_area = area
            best_angle = angle


    best_fit_points = rotate_points(xy_points, best_angle)
    min_x, max_x = min(best_fit_points[:, 0]), max(best_fit_points[:, 0])
    min_y, max_y = min(best_fit_points[:, 1]), max(best_fit_points[:, 1])
    print(f'Best fit bounding box: ({min_x}, {min_y}), ({max_x}, {max_y})')
    bounding_box = np.array([
        [min_x, min_y],
        [min_x, max_y],
        [max_x, max_y],
        [max_x, min_y],
        [min_x, min_y]  # Close the box
    ])

    rotated_bounding_box = rotate_points(bounding_box, -best_angle)

    return rotated_bounding_box, min_area, best_angle

class FieldPlotter(tk.Toplevel):  # Inherit from Toplevel instead of Tk
    def __init__(self, master=None):  # Optionally accept a master parameter
        super().__init__(master=master)  # Pass master to the Toplevel constructor
        self.title('Field Plotter')
        self.data = []  # List to store rectangles and metadata
        self.rectangles = []  # List to store rectangle patches
        self.title('Field Plotter')
        self.geometry('800x600')

        # Load and Plot Button
        self.plot_button = tk.Button(self, text="Load and Plot Field", command=self.load_and_plot)
        self.plot_button.pack()

        # Update Plot Button
        self.update_button = tk.Button(self, text="Update Plot", command=self.update_plot)
        self.update_button.pack()

        # Checkbox for Averaging
        self.average_var = tk.BooleanVar()
        self.average_checkbox = tk.Checkbutton(self, text="Average Close Positions", variable=self.average_var)
        self.average_checkbox.pack()

        # Entry for Specified Value
        self.specified_value_label = tk.Label(self, text="Distance Threshold (meters):")
        self.specified_value_label.pack()
        self.specified_value_entry = tk.Entry(self)
        self.specified_value_entry.pack()

        # Set a default value for the distance threshold entry
        default_threshold_value = "1.5"  # Default distance threshold in meters
        self.specified_value_entry.insert(0, default_threshold_value)

        # Add these in your __init__ method to create a new checkbox
        self.include_all_var = tk.BooleanVar()
        self.include_all_checkbox = tk.Checkbutton(self, text="Include All Waypoints", variable=self.include_all_var)
        self.include_all_checkbox.pack()

        # Add these in your __init__ method to create a new checkbox
        self.show_arrows_var = tk.BooleanVar()
        self.show_arrows_checkbox = tk.Checkbutton(self, text="Show waypoints", variable=self.show_arrows_var)
        self.show_arrows_checkbox.pack()

        # Setup for plot
        self.fig, self.ax = plt.subplots()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)  # A tk.DrawingArea.
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=1)

        self.filepath = None

    def save_to_json(self, filename):
        with open(filename, 'w') as f:
            json.dump(self.data, f)

    def load_from_json(self, filename):
        with open(filename) as f:
            self.data = json.load(f)
        self.plot_from_data()

    def load_and_plot(self):
        self.filepath = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if self.filepath:
            self.plot_field(self.filepath)

    def update_plot(self):
        if self.filepath:
            self.plot_field(self.filepath)

    import numpy as np

    def average_positions(self, points, threshold):
        averaged_points = []
        visited = [False] * len(points)

        for i in range(len(points)):
            if not visited[i]:
                close_points = [points.iloc[i]]
                visited[i] = True
                for j in range(i + 1, len(points)):
                    if not visited[j] and self.is_close(points.iloc[i], points.iloc[j], threshold):
                        close_points.append(points.iloc[j])
                        visited[j] = True

                # Align angles before averaging
                angles = [p['Angle'] for p in close_points]
                aligned_angles = self.align_angles(angles)

                # Calculate the average position for the group
                avg_x = np.mean([p['X'] for p in close_points])
                avg_y = np.mean([p['Y'] for p in close_points])

                # Average the aligned angles
                avg_angle = self.average_angles(aligned_angles)

                averaged_points.append({
                    'X': avg_x,
                    'Y': avg_y,
                    'Angle': avg_angle,
                    'Type': close_points[0]['Type'],
                    'MeasureSide': close_points[0]['MeasureSide'],
                    'Name': f"Avg_{close_points[0]['Name']}"
                })

        return pd.DataFrame(averaged_points)

    def align_angles(self, angles_rad):
        """
        Align angles in radians so they point in the same half-circle.
        """
        base_angle_rad = angles_rad[0]
        aligned_angles_rad = [base_angle_rad]

        for angle_rad in angles_rad[1:]:
            angle_diff = (angle_rad - base_angle_rad + np.pi) % (2 * np.pi) - np.pi
            if angle_diff > np.pi / 2:
                aligned_angle_rad = (angle_rad - np.pi) % (2 * np.pi)
            elif angle_diff < -np.pi / 2:
                aligned_angle_rad = (angle_rad + np.pi) % (2 * np.pi)
            else:
                aligned_angle_rad = angle_rad
            aligned_angles_rad.append(aligned_angle_rad)

        return aligned_angles_rad

    def average_angles(self, angles_rad):
        """
        Average a list of angles in radians, properly handling their circular nature.
        """
        sin_sum = np.sum(np.sin(angles_rad))
        cos_sum = np.sum(np.cos(angles_rad))
        avg_angle_rad = np.arctan2(sin_sum, cos_sum)
        return avg_angle_rad
    def is_close(self, point1, point2, threshold):
        distance = np.sqrt((point1['X'] - point2['X']) ** 2 + (point1['Y'] - point2['Y']) ** 2)
        return distance <= threshold
    def plot_field_(self, filepath):
        # Load data with the Z coordinate included
        data = pd.read_csv(filepath, header=None, names=['X', 'Y', "Z", 'Angle', 'Type', 'MeasureSide', 'Name'])
        #measure_points = data[data['Type'] == 'Measure']

        # Conditionally filter data based on the checkbox
        if self.include_all_var.get():
            points_to_plot = data
        else:
            points_to_plot = data[data['Type'] == 'Measure']

        if self.average_var.get():
            threshold = float(self.specified_value_entry.get())
            points_to_plot = self.average_positions(points_to_plot, threshold)
        measure_points = points_to_plot
        # Plot setup
        self.ax.clear()
        self.ax.set_aspect('equal', adjustable='box')
        chamber_points = []
        for index, row in measure_points.iterrows():
            angle = row['Angle']
            # Calculate offsets for the chambers based on robot orientation
            # Adjust these values as per your requirement to position one ahead and one behind
            dx_ahead = np.cos(angle) * 0.2  # 0.2m ahead
            dy_ahead = np.sin(angle) * 0.2
            dx_side = np.sin(angle) * 2  # 2m to the side
            dy_side = -np.cos(angle) * 2
            if row['Type'] == 'Measure':
                color = "black"
            if row['Type'] == 'DriveThrough':
                color = "red"
            if row['Type'] == 'TurningPoint':
                color = "blue"

            # Plot robot position with a specific marker, e.g., a black dot ('ko')
            #self.ax.plot(row['X'], row['Y'], color = color)  # Black dot for robot position

            # Add measurement number next to the robot position
            #self.ax.text(row['X'], row['Y'], str(index), color = color)

            # Draw an arrow indicating the driving direction
            #self.ax.arrow(row['X'], row['Y'], dx_ahead * 0.5, dy_ahead * 0.5, head_width=0.5, head_length=1.5, fc=color, ec=color)

            if row['Type'] == 'Measure':
                # Adjust chamber position calculation
                if row['MeasureSide'] == 'left' or row['MeasureSide'] == 'both':
                    # For left side, position the chamber
                    chamber_x_left, chamber_y_left = (row['X'] - dx_side + dx_ahead, row['Y'] - dy_side + dy_ahead)
                    chamber_points.append([chamber_x_left, chamber_y_left])
                    #self.ax.plot(chamber_x_left, chamber_y_left, 'gs')  # Green square for left chamber

                if row['MeasureSide'] == 'right' or row['MeasureSide'] == 'both':
                    # For right side, position the chamber
                    chamber_x_right, chamber_y_right = (row['X'] + dx_side + dx_ahead, row['Y'] + dy_side + dy_ahead)
                    chamber_points.append([chamber_x_right, chamber_y_right])
                    #self.ax.plot(chamber_x_right, chamber_y_right, 'rs')  # Red square for right chamber

        chamber_points = np.array(chamber_points)

        # Find the minimum bounding box for the given points
        rotated_bounding_box, min_area, best_angle = find_minimum_bounding_box(chamber_points)

        self.ax.plot(rotated_bounding_box[:, 0], rotated_bounding_box[:, 1], 'r--',
                 label=f'Minimum Area Bounding Box{min_area}')

        print(chamber_points)

        self.canvas.draw()
    def plot_field(self, filepath):
        # Load data with the Z coordinate included
        data = pd.read_csv(filepath, header=None, names=['X', 'Y', "Z", 'Angle', 'Type', 'MeasureSide', 'Name'])
        #measure_points = data[data['Type'] == 'Measure']

        # Conditionally filter data based on the checkbox
        if self.include_all_var.get():
            points_to_plot = data
        else:
            points_to_plot = data[data['Type'] == 'Measure']

        if self.average_var.get():
            threshold = float(self.specified_value_entry.get())
            points_to_plot = self.average_positions(points_to_plot, threshold)
        measure_points = points_to_plot
        # Plot setup
        self.ax.clear()
        self.ax.set_aspect('equal', adjustable='box')
        chamber_points = []
        for index, row in measure_points.iterrows():
            angle = row['Angle']
            # Calculate offsets for the chambers based on robot orientation
            # Adjust these values as per your requirement to position one ahead and one behind
            dx_ahead = np.cos(angle) * 0.2  # 0.2m ahead
            dy_ahead = np.sin(angle) * 0.2
            dx_side = np.sin(angle) * 2  # 2m to the side
            dy_side = -np.cos(angle) * 2
            if row['Type'] == 'Measure':
                color = "black"
            if row['Type'] == 'DriveThrough':
                color = "red"
            if row['Type'] == 'TurningPoint':
                color = "blue"

            # Plot robot position with a specific marker, e.g., a black dot ('ko')
            self.ax.plot(row['X'], row['Y'], color = color)  # Black dot for robot position

            # Add measurement number next to the robot position
            self.ax.text(row['X'], row['Y'], str(index), color = color)

            # Draw an arrow indicating the driving direction
            self.ax.arrow(row['X'], row['Y'], dx_ahead * 0.5, dy_ahead * 0.5, head_width=0.5, head_length=1.5, fc=color,
                          ec=color)

            if row['Type'] == 'Measure':
                # Adjust chamber position calculation
                if row['MeasureSide'] == 'left' or row['MeasureSide'] == 'both':
                    # For left side, position the chamber
                    chamber_x_left, chamber_y_left = (row['X'] - dx_side + dx_ahead, row['Y'] - dy_side + dy_ahead)
                    chamber_points.append([chamber_x_left, chamber_y_left])
                    self.ax.plot(chamber_x_left, chamber_y_left, 'gs')  # Green square for left chamber

                if row['MeasureSide'] == 'right' or row['MeasureSide'] == 'both':
                    # For right side, position the chamber
                    chamber_x_right, chamber_y_right = (row['X'] + dx_side + dx_ahead, row['Y'] + dy_side + dy_ahead)
                    chamber_points.append([chamber_x_right, chamber_y_right])
                    self.ax.plot(chamber_x_right, chamber_y_right, 'rs')  # Red square for right chamber

        chamber_points = np.array(chamber_points)

        # Find the minimum bounding box for the given points
        rotated_bounding_box, min_area, best_angle = find_minimum_bounding_box(chamber_points)
        self.rectangles = find_rectangles(chamber_points, best_angle)
        for rect in self.rectangles:
            polygon = patches.Polygon(rect, closed=True, fill=None, edgecolor='r', linestyle='--')
            self.ax.add_patch(polygon)

        self.ax.plot(rotated_bounding_box[:, 0], rotated_bounding_box[:, 1], 'r--',
                 label=f'Minimum Area Bounding Box{min_area}',alpha=0.2)

        self.canvas.draw()

if __name__ == "__main__":
    root = tk.Tk()  # Create a root window if standalone
    root.withdraw()  # Optionally hide the root window
    app = FieldPlotter(root)  # Pass the root window as the master
    app.mainloop()