#Load and evaluate shadow models, extract probabilities for train and test data, filter duplicate entries, and save attack data.
import os
import torch
import numpy as np
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
import torchvision.transforms as transforms
from torch import nn
from PIL import Image

BASE_DIR = "/home/lab24inference/amelie/shadow_models/synthetic_cifar_models"
SHADOW_DATA_DIR = "/home/lab24inference/amelie/shadow_models_data/fake_cifar/shadow_data"
MODEL_SAVE_DIR = os.path.join(BASE_DIR, "models")
OUTPUT_DIR = os.path.join(BASE_DIR, "attack_data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# check if gpu is available
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

# shadow model definition
class ShadowModel(nn.Module):
    def __init__(self):
        super(ShadowModel, self).__init__()
        self.conv_layers = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size=3, padding=1),
            nn.Tanh(),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.Tanh(),
            nn.MaxPool2d(2, 2)
        )

        self.fc_layers = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 8 * 8, 128),
            nn.Tanh(),
            nn.Linear(128, 10)  # ! No softmax here (CrossEntropyLoss used)
        )
    def forward(self, x):
        x = self.conv_layers(x)
        x = self.fc_layers(x)
        return x

# Dataset-class
class ShadowDataset(Dataset):
    def __init__(self, data_path):
        data = np.load(data_path)
        self.images = data["images"] 
        self.labels = data["labels"]

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        image = self.images[idx].astype(np.float32) / 255.0
        label = self.labels[idx]
        transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        ])
        return transform(image), label

# Evaluate models
def evaluate_model(model, test_loader):
    correct = 0
    total = 0
    with torch.no_grad():
        for inputs, targets in test_loader:
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += (predicted == targets).sum().item()
    accuracy = correct / total * 100
    print(f"Test Accuracy: {accuracy:.2f}%")
    return accuracy

#extract probabilities and labels
def extract_probabilities(shadow_id, train_loader, test_loader):
    model = ShadowModel().to(device)
    model_path = os.path.join(MODEL_SAVE_DIR, f"shadow_model_{shadow_id}.pth")

    # Load safed shadow model
    if not os.path.exists(model_path):
        print(f"Model {shadow_id} not found. Skipping.")
        return

    model.load_state_dict(torch.load(model_path))
    model.eval()

    # Evaluate models
    print(f"Evaluating Shadow Model {shadow_id}...")
    evaluate_model(model, test_loader)

    def get_outputs(loader, member_label):
        probabilities = []
        labels = []
        member_labels = []

        with torch.no_grad():
            for inputs, targets in loader:
                inputs = inputs.to(device)
                outputs = model(inputs)
                probs = F.softmax(outputs, dim=1).cpu().numpy()

                # Check if sum of probabilities = 1
                assert np.allclose(probs.sum(axis=1), 1.0), "Probabilities do not sum up to 1!"

                probabilities.append(probs)
                labels.append(targets.numpy())
                member_labels.extend([member_label] * len(targets))

        return np.vstack(probabilities), np.hstack(labels), np.array(member_labels)

    #compute confidence scores for train and test
    train_probs, train_labels, train_members = get_outputs(train_loader, member_label=1)
    test_probs, test_labels, test_members = get_outputs(test_loader, member_label=0)

    
    probabilities = np.vstack((train_probs, test_probs))
    labels = np.hstack((train_labels, test_labels))
    members = np.hstack((train_members, test_members))

    # Delete duplicates
    unique_data = {}
    filtered_probs = []
    filtered_labels = []
    filtered_members = []

    for prob, label, member in zip(probabilities, labels, members):
        prob_tuple = tuple(prob)
        if prob_tuple not in unique_data:
            unique_data[prob_tuple] = member
            filtered_probs.append(prob)
            filtered_labels.append(label)
            filtered_members.append(member)
        elif unique_data[prob_tuple] != member:
            # Skip dublicates
            print(f"Konflikt gefunden bei Wahrscheinlichkeiten {prob_tuple} (Mitgliedschaft: {unique_data[prob_tuple]} vs {member}). Überspringe.")

    probabilities = np.array(filtered_probs)
    labels = np.array(filtered_labels)
    members = np.array(filtered_members)

    print(f"Final probabilities shape: {probabilities.shape}")
    print(f"Final labels shape: {labels.shape}")
    print(f"Final members shape: {members.shape}")

    
    # Safe results
    output_path = os.path.join(OUTPUT_DIR, f"shadow_model_{shadow_id}_attack_data.npz")
    np.savez(output_path, probabilities=probabilities, labels=labels, members=members)
    print(f"Attack data for Shadow Model {shadow_id} saved to {output_path}.")

if __name__ == "__main__":
    for shadow_id in range(1,31):  # Shadow Models Nr 1 - 30
        train_data_path = os.path.join(SHADOW_DATA_DIR, f"shadow_model_{shadow_id}/train/train_data.npz")
        test_data_path = os.path.join(SHADOW_DATA_DIR, f"shadow_model_{shadow_id}/test/test_data.npz")

        if not os.path.exists(train_data_path) or not os.path.exists(test_data_path):
            print(f"Data for Shadow Model {shadow_id} not found. Skipping.")
            continue

        train_dataset = ShadowDataset(train_data_path)
        test_dataset = ShadowDataset(test_data_path)

        train_loader = DataLoader(train_dataset, batch_size=256, shuffle=False, num_workers=4)
        test_loader = DataLoader(test_dataset, batch_size=256, shuffle=False, num_workers=4)

        print(f"Extracting attack data for Shadow Model {shadow_id}...")
        extract_probabilities(shadow_id, train_loader, test_loader)
