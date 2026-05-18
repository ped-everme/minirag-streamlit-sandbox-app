# 1. Use a lightweight Python 3.11 base image
FROM python:3.11-slim

# 2. Set the working directory inside the container
WORKDIR /pipeline

# 3. Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy all project files into the container
COPY . .

# 5. Define the command to execute the deep research pipeline
CMD ["python", "-m", "src.deep_research.deep_research_pipeline", "--time_window", "last_three_months"]