import os


def combine_files(directories, output_file, extensions=(".py", ".qss", ".sh")):
    total_lines = 0
    all_code = ""

    for directory in directories:
        if os.path.exists(directory):
            for file in os.listdir(directory):
                if file.endswith(extensions):
                    file_path = os.path.join(directory, file)
                    file_name = os.path.basename(file_path)
                    print(f"Processing {file_name}...")
                    with open(file_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        total_lines += len(lines)
                        all_code += (
                            f"\n# File: {file} ----------------------------------------------------------------------\n"
                            + "".join(lines)
                        )

    # Save combined content to a new file
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(all_code)

    print(f"{output_file} created with {total_lines} total lines.")


def main():
    # Define root directory
    root_dir = os.getcwd()  # Change this to your project path
    server_dir = os.path.join(root_dir, "server")
    templates_dir = os.path.join(server_dir, "templates")
    static_dir = os.path.join(server_dir, "static")
    js_dir = os.path.join(static_dir, "js")
    css_dir = os.path.join(static_dir, "css")
    output_file = r"TestCodes\\combined\\all_combined.py"

    # Combine all files with the specified extensions into one
    directories_to_combine = []
    # directories_to_combine.append(root_dir)
    directories_to_combine.append(server_dir)
    directories_to_combine.append(templates_dir)
    directories_to_combine.append(static_dir)
    directories_to_combine.append(js_dir)
    directories_to_combine.append(css_dir)

    extensions_to_include = (".py", ".html", ".js", ".css")

    combine_files(directories_to_combine, output_file, extensions=extensions_to_include)


if __name__ == "__main__":
    main()
