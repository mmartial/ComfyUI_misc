import os
import signal
import json
import argparse
import sys
from pathlib import Path
from collections import defaultdict
import shutil

# Configuration
MODEL_EXTENSIONS = {'.safetensors', '.pth', '.bin', '.ckpt', '.gguf', '.pt', '.sft'}
# Designed to work with both Stability Matric and LoRA Manager for sidecar files
SIDECAR_EXTENSIONS = {'.preview.jpg', '.preview.png', '.civitai.info', '.cm-info.json', '.metadata.json', '.preview.jpeg', '.json', '.sha256', '.info', '.png', '.jpg', '.jpeg', '.yaml', '.txt', '.xml', '.webp', '.mp4'}

# Pre-computed sorted list for matching longest extensions first
ALL_EXTENSIONS = []

# The rest of the configuration is loaded from safetensor_cleaner.json
# basic structure of safetensor_cleaner.json is:
# {
#     "ignore_extensions": [".py"],
#     "ignore_folders": ["IpAdapter", "IpAdaptersXl", "IpAdapters15", "ControlNet", "ClipVision", "VAE", "ApproxVAE"],
#     "ignore_groups": ["Dramatic Lighting Slider"]
# }


# Colors
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    
    # Extension colors
    EXT_MODEL = '\033[92m' # Green
    EXT_SIDECAR = '\033[96m' # Cyan
    EXT_UNKNOWN = '\033[93m' # Yellow
    OK_ORPHAN = '\033[94m' # Blue

# Ignore this script and any .py files
IGNORE_EXTENSIONS = set()
# Ignore folders
IGNORE_FOLDERS = set()
# Ignore groups
IGNORE_GROUPS = set()
# Ignore specific files
IGNORE_FILES = {'safetensor_cleaner.py', 'safetensor_cleaner.json'}

def load_config():
    """Loads configuration from safetensor_cleaner.json if it exists."""
    config_path = Path(__file__).parent / 'safetensor_cleaner.json'
    global ALL_EXTENSIONS
    
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                
            global IGNORE_EXTENSIONS, IGNORE_FOLDERS, IGNORE_GROUPS
            
            if 'ignore_extensions' in config:
                IGNORE_EXTENSIONS.update(config['ignore_extensions'])
            if 'ignore_folders' in config:
                IGNORE_FOLDERS.update(config['ignore_folders'])
            if 'ignore_groups' in config:
                IGNORE_GROUPS.update(config['ignore_groups'])
            if 'ignore_files' in config:
                IGNORE_FILES.update(config['ignore_files'])
                
            print(f"{Colors.OKBLUE}Loaded configuration from {config_path.name}{Colors.ENDC}")
        except Exception as e:
            print(f"{Colors.FAIL}Error loading config: {e}{Colors.ENDC}")

    # Initialize ALL_EXTENSIONS after config might have modified things (though currently it doesn't modify the sets)
    ALL_EXTENSIONS[:] = sorted(list(MODEL_EXTENSIONS | SIDECAR_EXTENSIONS), key=len, reverse=True)

# Load config immediately
load_config()

def get_file_stem(filename):
    """Returns the base name (stem) by stripping the longest known extension."""
    for ext in ALL_EXTENSIONS:
        if filename.endswith(ext):
            return filename[:-len(ext)], ext
    return None, None

def get_file_type(filename):
    """Returns 'model', 'sidecar', or 'other'."""
    for ext in MODEL_EXTENSIONS:
        if filename.endswith(ext):
            return 'model'
    for ext in SIDECAR_EXTENSIONS:
        if filename.endswith(ext):
            return 'sidecar'
    return 'other'



def group_files_by_stem(file_list):
    """Groups files by their base name (stem) by stripping known extensions."""
    groups = defaultdict(list)

    for file_path in file_list:
        # Check for ignored files
        if file_path.name in IGNORE_FILES:
            continue
            
        # Check for ignored extensions
        should_skip = False
        for ext in IGNORE_EXTENSIONS:
            if file_path.name.endswith(ext):
                should_skip = True
                break
        if should_skip:
            continue

        # Try to strip all extensions to find the base name
        base_name, _ = get_file_stem(file_path.name)
        
        if base_name:
            groups[base_name].append(file_path)
        else:
            groups['unknown'].append(file_path)

    # Remove ignored groups
    for ignored in IGNORE_GROUPS:
        if ignored in groups:
            del groups[ignored]

    return groups

def detect_versions(groups):
    """
    Decomposes group stems by '_' to find common bases (versions of same model).
    Returns a dict: base_name -> list of original_stems
    Only includes bases that match multiple stems where at least one stem has a model.
    """
    version_map = defaultdict(list)
    
    # helper: check if a group has a model file
    def group_has_model(stem):
        files = groups.get(stem, [])
        for f in files:
            if get_file_type(f.name) == 'model':
                return True
        return False

    for stem in groups.keys():
        if stem == 'unknown': continue
        
        parts = stem.split('_')
        # Generate candidates: "A_B_C" -> "A", "A_B"
        candidates = []
        for i in range(1, len(parts) + 1):
            candidates.append('_'.join(parts[:i]))
            
        for base in candidates:
            if base in IGNORE_GROUPS:
                continue
            version_map[base].append(stem)

    # Filter useful versions
    # Rule 1: Must have > 1 variant for this base
    # Rule 2: At least one of the variants must actually contain a model file. 
    #         (Otherwise we just grouping random orphan sidecars)
    final_map = {}
    for base, stems in version_map.items():
        if len(stems) > 1:
            has_model = any(group_has_model(s) for s in stems)
            if has_model:
                final_map[base] = sorted(stems)

    # Filter redundant bases (e.g. 'Urd' if 'Urd_from' has exact same stems)
    # Iterate longest bases first
    sorted_bases = sorted(final_map.keys(), key=len, reverse=True)
    to_remove = set()
    for i, base_long in enumerate(sorted_bases):
        if base_long in to_remove: continue
        for base_short in sorted_bases[i+1:]:
            if base_short in to_remove: continue
            
            if base_long.startswith(base_short):
                # Check if they point to the exact same set of stems
                if set(final_map[base_long]) == set(final_map[base_short]):
                    to_remove.add(base_short)
    
    for k in to_remove:
        if k in final_map:
            del final_map[k]
                
    return final_map

def check_orphans_against_versions(groups, version_map):
    """
    Checks if orphan groups might belong to a detected version family.
    """
    potential_matches = {} # orphan_stem -> matched_base
    
    # Identify orphan groups
    orphan_groups = []
    for stem, files in groups.items():
        if stem == 'unknown': continue
        
        has_model = False
        for f in files:
            for ext in MODEL_EXTENSIONS:
                if f.name.endswith(ext):
                    has_model = True
                    break
        if not has_model:
            orphan_groups.append(stem)
            
    # Check each orphan
    for orphan in orphan_groups:
        # Check if orphan IS a base (e.g. Model.json vs Model_v1.safetensors)
        if orphan in version_map:
            potential_matches[orphan] = orphan
            continue
            
        # Check if orphan is a prefix of a known base (unlikely if split by _ correctly)
        # Check if orphan matches one of the versions
        for base in version_map:
            if orphan.startswith(base):
                potential_matches[orphan] = base
                
    return potential_matches

def highlight_extension(filename):
    """Returns the filename with the extension colorized."""
    base, ext = get_file_stem(filename)
    if base and ext:
        ftype = get_file_type(filename)
        if ftype == 'model':
            color = Colors.EXT_MODEL
        elif ftype == 'sidecar':
            color = Colors.EXT_SIDECAR
        else:
            color = Colors.EXT_UNKNOWN # Should not happen if get_file_stem worked with ALL_EXTENSIONS
            
        return f"{base}{color}{ext}{Colors.ENDC}"
    return filename

def get_files_recursively(root_dir):
    """Scans the directory recursively and returns a list of Path objects."""
    file_list = []
    print(f"Scanning {root_dir}...")
    try:
        for root, dirs, files in os.walk(root_dir):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if d not in IGNORE_FOLDERS]
            
            for file in files:
                file_list.append(Path(root) / file)
    except Exception as e:
        print(f"Error scanning directory: {e}")
    return file_list

def confirm_action(prompt):
    """Asks user for confirmation. Returns True if confirmed."""
    while True:
        try:
            response = input(f"{Colors.WARNING}{prompt}{Colors.ENDC} [y/N]: ").strip().lower()
            if response == 'y':
                return True
            if response == 'n' or response == '':
                return False
        except KeyboardInterrupt:
            print("\nOperation cancelled by user.")
            sys.exit(1)

def categorize_group(files):
    """Separates a list of files into models and sidecars."""
    models = []
    sidecars = []
    others = []
    
    for f in files:
        ftype = get_file_type(f.name)
        if ftype == 'model':
            models.append(f)
        elif ftype == 'sidecar':
            sidecars.append(f)
        else:
            others.append(f)
            
    return models, sidecars, others

def process_groups(groups, args, root_path):
    """Analyzes and processes the file groups based on arguments."""
    

    
    # Version Detection Mode
def handle_versions_mode(groups, root_path):
    print(f"\n{Colors.BOLD}--- DETECTING VERSIONS ---{Colors.ENDC}")
    version_map = detect_versions(groups)
    orphan_matches = check_orphans_against_versions(groups, version_map)
    
    if not version_map:
        print("No multi-version models detected.")
    else:
        # Sort by Top-Level Folder, then Base Name
        def version_sort_key(item):
            base, stems = item
            # Find a representative file to determine the folder
            for stem in stems:
                for f in groups[stem]:
                    # Try to find a model file first
                    if get_file_type(f.name) == 'model':
                        try:
                            # Get top-level folder relative to root
                            rel = f.parent.relative_to(root_path)
                            if len(rel.parts) > 0:
                                return (rel.parts[0], base)
                            return ("", base) # File in root
                        except ValueError:
                            continue # Should not happen if f is from root_path scan
            
            # Fallback: check any file if no model found
            for stem in stems:
                if groups[stem]:
                    f = groups[stem][0]
                    try:
                        rel = f.parent.relative_to(root_path)
                        if len(rel.parts) > 0:
                            return (rel.parts[0], base)
                        return ("", base)
                    except ValueError:
                        pass
                            
            return ("", base)

        for base, stems in sorted(version_map.items(), key=version_sort_key):
            print(f"\n{Colors.HEADER}Model Group: {base}{Colors.ENDC}")
            for stem in stems:
                files = groups[stem]
                # Check if this specific stem is mainly models or sidecars
                has_model = any(get_file_type(f.name) == 'model' for f in files)
                file_count = len(files)
                
                status = f"{Colors.OKGREEN}[MODEL]{Colors.ENDC}" if has_model else f"{Colors.FAIL}[ORPHAN]{Colors.ENDC}"
                
                print(f"  - {stem} ({file_count} files) {status}")
                for f in files:
                    print(f"      {highlight_extension(f.name)} [{Colors.OKBLUE}{f.parent}{Colors.ENDC}]")
                
    if orphan_matches:
        print(f"\n{Colors.BOLD}--- POTENTIAL ORPHAN MATCHES ---{Colors.ENDC}")
        for orphan, base in sorted(orphan_matches.items()):
            print(f"  {Colors.OK_ORPHAN}{orphan}{Colors.ENDC} seems related to group {Colors.HEADER}{base}{Colors.ENDC}")

def handle_cleanup_mode(groups, args):
    """Handles standard cleanup operations: Orphans, Duplicates, and Moves."""
    files_moved = 0
    orphans_deleted = 0
    duplicates_deleted = 0

    # Sort groups by stem for consistent output
    sorted_stems = sorted([k for k in groups.keys() if k != 'unknown'])
    if 'unknown' in groups:
        sorted_stems.append('unknown')
        
    for stem in sorted_stems:
        files = groups[stem]
        models, sidecars, others = categorize_group(files)
        
        # Determine strict matching logic
        has_orphans = False
        has_duplicates = False
        has_moves = False
        
        # Check Orphans
        if not models and sidecars:
            has_orphans = True
            
        # Check Models present
        target_dir = None
        duplicates_list = [] # List of lists of dupes
        moves_list = []
        
        if models:
            target_dir = models[0].parent
            
            # Check Duplicates
            sidecars_by_ext = defaultdict(list)
            for s in sidecars:
                _, matched_ext = get_file_stem(s.name)
                if matched_ext:
                    sidecars_by_ext[matched_ext].append(s)
            
            for ext, s_list in sidecars_by_ext.items():
                if len(s_list) > 1:
                    has_duplicates = True
                    duplicates_list.append((ext, s_list))
            
            # Check Moves (only valid existing sidecars)
            for sidecar in sidecars:
                if sidecar.exists() and sidecar.parent != target_dir:
                    has_moves = True
                    moves_list.append(sidecar)

        # Decision to print group
        action_needed = has_orphans or has_duplicates or has_moves
        should_print = args.verbose or action_needed
        
        if stem == 'unknown' and args.show_unknown:
            should_print = True
        
        if not should_print:
            continue

        # Print Group Header
        print(f"\n{Colors.HEADER}Group: {stem}{Colors.ENDC}")
        
        # Track unknown extensions if this is the unknown group
        unknown_exts = set()
        
        for f in files:
            # Re-verify existence for display? Logic assumes files exist from scan.
            print(f"  - {highlight_extension(f.name)} ({f.parent})")
            if stem == 'unknown':
                unknown_exts.add(f.suffix)

        if stem == 'unknown' and unknown_exts:
            print(f"\n{Colors.BOLD}Unknown Extensions found: {', '.join(sorted(unknown_exts))}{Colors.ENDC}")

        # --- EXECUTE ACTIONS ---

        # 1. Orphans
        if has_orphans:
            if args.verbose or True: # Orphans are always notable if we are printing the group
                print(f"  {Colors.FAIL}[ORPHAN]{Colors.ENDC} No model found.")
            
            if args.delete_orphan:
                for sidecar in sidecars:
                    if args.confirm_each:
                        if not confirm_action(f"Delete orphan {sidecar.name}?"):
                            continue
                    try:
                        print(f"  {Colors.FAIL}Deleting orphan: {sidecar.name}{Colors.ENDC}")
                        os.remove(sidecar)
                        orphans_deleted += 1
                    except OSError as e:
                        print(f"  Error deleting {sidecar.name}: {e}")

        # 2. Duplicates
        if has_duplicates:
            for ext, s_list in duplicates_list:
                print(f"  {Colors.WARNING}[DUPLICATE]{Colors.ENDC} Found {len(s_list)} files for extension {ext}")
                
                if args.delete_duplicates:
                    # Preference: Same dir as model
                    keep = None
                    for s in s_list:
                        if s.parent == target_dir:
                            keep = s
                            break
                    if not keep:
                        keep = s_list[0] 
                        
                    for s in s_list:
                        if s != keep:
                            if args.confirm_each:
                                if not confirm_action(f"Delete duplicate {s.name} (keeping {keep.name})?"):
                                    continue
                            try:
                                print(f"  {Colors.FAIL}Deleting duplicate: {s.name}{Colors.ENDC}")
                                os.remove(s)
                                duplicates_deleted += 1
                            except OSError as e:
                                print(f"  Error deleting {s}: {e}")

        # 3. Moves
        if has_moves:
            # Refresh moves list if we deleted stuff? 
            # Ideally re-check existence.
            valid_moves = [m for m in moves_list if m.exists()]
            
            for sidecar in valid_moves:
                print(f"  {Colors.OKCYAN}[MOVE]{Colors.ENDC} {sidecar.name} -> {target_dir}")
                
                if args.move:
                    dest_path = target_dir / sidecar.name
                    if dest_path.exists():
                        print(f"    Skipping move: Destination {dest_path.name} already exists.")
                        continue
                        
                    if args.confirm_each:
                        if not confirm_action(f"Move {sidecar.name} to {target_dir}?"):
                            continue
                    
                    try:
                        shutil.move(str(sidecar), str(dest_path))
                        files_moved += 1
                        print(f"    {Colors.OKGREEN}Moved successfully.{Colors.ENDC}")
                    except OSError as e:
                        print(f"    Error moving {sidecar.name}: {e}")

    # Summary
    if not (args.move or args.delete_orphan or args.delete_duplicates):
        print(f"\n{Colors.BOLD}--- DRY RUN COMPLETE ---{Colors.ENDC}")
        print("No changes were made. Use --move, --delete_orphan, or --delete_duplicates to apply changes.")
    else:
        print(f"\n{Colors.BOLD}--- OPERATION COMPLETE ---{Colors.ENDC}")
        print(f"Moved: {files_moved}")
        print(f"Orphans Deleted: {orphans_deleted}")
        print(f"Duplicates Deleted: {duplicates_deleted}")

def process_groups(groups, args, root_path):
    """Analyzes and processes the file groups based on arguments."""
    
    # Version Detection Mode
    if args.show_versions:
        handle_versions_mode(groups, root_path)
        return

    # Standard Cleanup Mode
    handle_cleanup_mode(groups, args)

def main():
    parser = argparse.ArgumentParser(description="Reorganize model sidecar files.")
    parser.add_argument("--root", type=str, default=".", help="Root directory to scan (default: current)")
    parser.add_argument("--move", action="store_true", help="Move sidecars to the model's directory")
    parser.add_argument("--delete_orphan", action="store_true", help="Delete sidecars that have no corresponding model")
    parser.add_argument("--delete_duplicates", action="store_true", help="Delete duplicate sidecars (keeps one near model)")
    parser.add_argument("--confirm-each", action="store_true", help="Interactive mode: Ask before every action")
    parser.add_argument("--verbose", action="store_true", help="Show all groups, even those without actions")
    parser.add_argument("--show-versions", action="store_true", help="Show multiple versions of models and related orphans")
    parser.add_argument("--show-unknown", action="store_true", help="Show files that were not categorized into groups")
    
    args = parser.parse_args()
    
    root_path = Path(args.root).resolve()
    if not root_path.exists():
        print(f"Error: Directory {root_path} does not exist.")
        return

    files = get_files_recursively(root_path)
    if not files:
        print("No files found.")
        return
        
    groups = group_files_by_stem(files)
    process_groups(groups, args, root_path)

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL) 
    except AttributeError:
        # Windows does not have SIGPIPE
        pass
    main()
