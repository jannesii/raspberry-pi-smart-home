document.addEventListener('DOMContentLoaded', function() {
  const tempCheckbox = document.getElementById('is_temporary');
  const tempFields = document.getElementById('temp_duration_fields');
  if (!tempCheckbox || !tempFields) return;

  const toggle = () => {
    tempFields.style.display = tempCheckbox.checked ? 'inline-flex' : 'none';
  };
  tempFields.style.gap = '10px';
  tempFields.style.alignItems = 'center';

  tempCheckbox.addEventListener('change', toggle);
  toggle();
});
