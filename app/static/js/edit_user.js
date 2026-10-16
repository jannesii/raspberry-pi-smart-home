document.addEventListener('DOMContentLoaded', function() {
  const isTemporary = document.getElementById('is_temporary');
  const expiryFields = document.getElementById('expiryFields');

  if (isTemporary && expiryFields) {
    const toggle = () => {
      expiryFields.style.display = isTemporary.checked ? 'grid' : 'none';
    };
    isTemporary.addEventListener('change', toggle);
    toggle();
  }
});
