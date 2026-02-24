# Use an official Python runtime as a parent image
# Consider using a PyTorch image if needed: FROM pytorch/pytorch:latest
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY . .

# Make port 8914 available to the world outside this container
EXPOSE 8914

# Define environment variable
ENV PORT=8914

# Run main.py when the container launches using Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8914"]
